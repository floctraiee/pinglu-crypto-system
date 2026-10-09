"""在仓库根目录执行 python -m unittest discover -s tests -p test_contract_integration.py -v。
使用临时 SQLite，不修改原有中心数据库。需已安装 gmssl、fastapi、httpx。
"""
import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from common_crypto.sm import generate_keypair, sign_message
from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.hash_chain import record_hash, ZERO_HASH
from common_crypto.merkle import merkle_root
from common_crypto.batch import verify_batch, PAYLOAD_FIELDS
from center_platform import storage
from center_platform.main import app


class ContractIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private, cls.public = generate_keypair()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_db = storage.DB_PATH
        storage.DB_PATH = Path(self.folder.name) / 'center.db'
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        storage.register_device('WL-001', 'water_level', self.public)
        self.registration = storage.get_device('WL-001')
        self.batch = self.make_batch()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        storage.DB_PATH = self.old_db
        self.folder.cleanup()

    def resign(self, batch):
        batch['signature'] = sign_message(self.private, self.public, canonical_bytes(signed_header(batch)))
        return batch

    def make_batch(self, number=1, start=1, previous=ZERO_HASH, kind='water_level', device='WL-001'):
        records=[]
        for sequence in range(start, start+3):
            stamp=datetime(2026,10,8,12,tzinfo=timezone(timedelta(hours=8)))+timedelta(seconds=sequence)
            record={
                'version':1,'device_id':device,'device_type':kind,'timestamp':stamp.isoformat(),
                'longitude':'108.500000','latitude':'22.000000','sequence':sequence,
                'batch_id':f'{device}-{number:06d}',
                'payload':{key:'1.00' for key in PAYLOAD_FIELDS[kind]},
                'status':'normal','previous_hash':previous,
            }
            record['record_hash']=record_hash(record)
            previous=record['record_hash']
            records.append(record)
        return self.resign({
            'version':1,'device_id':device,'batch_id':records[0]['batch_id'],
            'start_sequence':start,'end_sequence':start+2,'count':3,
            'start_time':records[0]['timestamp'],'end_time':records[-1]['timestamp'],
            'merkle_root':merkle_root([r['record_hash'] for r in records]),'records':records,
        })

    def post(self, batch):
        response=self.client.post('/batches',json={'batch':batch})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_normal_api_and_proof(self):
        self.assertEqual(self.post(self.batch)['status'],'accepted')
        self.assertEqual(self.client.get('/health').json()['status'],'ok')
        self.assertEqual(self.client.get('/stats').json()['records'],3)
        proof=self.client.get('/batches/WL-001-000001/proof/2').json()
        self.assertTrue(proof['verified'])
        self.assertEqual(proof['root'],self.batch['merkle_root'])
        self.assertEqual(storage.get_batch('WL-001','WL-001-000001')['raw'],self.batch)

    def test_all_seven_device_types(self):
        for index, kind in enumerate(PAYLOAD_FIELDS):
            with self.subTest(kind=kind):
                device=f'TEST-{index:03d}'
                registration={'device_id':device,'device_type':kind,'public_key':self.public}
                result=verify_batch(self.make_batch(kind=kind,device=device),registration)
                self.assertTrue(result['valid'],result)

    def test_value_tampering_locates_record(self):
        self.batch['records'][1]['payload']['water_level_m']='999.00'
        result=self.post(self.batch)
        self.assertEqual(result['reason_code'],'INVALID_RECORD_HASH')
        self.assertEqual(result['affected_sequences'],[2])
        self.assertEqual(storage.get_stats()['records'],0)
        self.assertEqual(storage.list_audits()[0]['raw'],self.batch)

    def test_bad_signature(self):
        self.batch['signature']='0'*128
        self.assertEqual(self.post(self.batch)['reason_code'],'INVALID_SIGNATURE')

    def test_recomputed_hash_still_fails_signed_root(self):
        self.batch['records'][2]['payload']['water_level_m']='999.00'
        self.batch['records'][2]['record_hash']=record_hash(self.batch['records'][2])
        self.assertEqual(self.post(self.batch)['reason_code'],'INVALID_MERKLE_ROOT')

    def test_count_tamper(self):
        self.batch['count']=4
        self.resign(self.batch)
        self.assertEqual(self.post(self.batch)['reason_code'],'INVALID_BATCH_RANGE')

    def test_broken_chain(self):
        self.batch['records'][2]['previous_hash']='1'*64
        self.batch['records'][2]['record_hash']=record_hash(self.batch['records'][2])
        self.batch['merkle_root']=merkle_root([r['record_hash'] for r in self.batch['records']])
        self.resign(self.batch)
        self.assertEqual(self.post(self.batch)['reason_code'],'INVALID_HASH_CHAIN')

    def test_cross_batch_and_reinitialize(self):
        self.assertEqual(self.post(self.batch)['status'],'accepted')
        storage.init_db()
        second=self.make_batch(2,4,self.batch['records'][-1]['record_hash'])
        self.assertEqual(self.post(second)['status'],'accepted')
        self.assertEqual(storage.get_stats()['records'],6)

    def test_sequence_gap(self):
        self.assertEqual(self.post(self.make_batch(start=4))['reason_code'],'INVALID_SEQUENCE')

    def test_unchanged_duplicate(self):
        self.post(self.batch)
        self.assertEqual(self.post(self.batch)['status'],'duplicate')
        self.assertEqual(storage.get_stats()['records'],3)
        self.assertEqual(storage.list_audits()[0]['reason_code'],'DUPLICATE_BATCH')

    def test_same_id_changed_content_is_conflict(self):
        self.post(self.batch)
        changed=copy.deepcopy(self.batch)
        changed['records'][0]['payload']['water_level_m']='99.00'
        self.assertEqual(self.post(changed)['reason_code'],'BATCH_CONTENT_CONFLICT')
        self.assertEqual(storage.get_stats()['records'],3)

    def test_unknown_device_audited(self):
        self.batch['device_id']='UNKNOWN'
        self.assertEqual(self.post(self.batch)['reason_code'],'UNKNOWN_DEVICE')
        self.assertEqual(storage.get_stats()['audits'],1)

    def test_old_header_rejected_audited(self):
        result=self.post({'header':signed_header(self.batch),'records':self.batch['records'],'signature':self.batch['signature']})
        self.assertEqual(result['reason_code'],'INVALID_BATCH_STRUCTURE')
        self.assertEqual(storage.get_stats()['audits'],1)

    def test_malformed_contract_records(self):
        for change in ('boolean_version','missing_status','numeric_payload','wrong_timezone','extra_field','invalid_hash','empty_records'):
            with self.subTest(change=change):
                batch=copy.deepcopy(self.batch)
                if change=='boolean_version':batch['version']=True
                if change=='missing_status':del batch['records'][0]['status']
                if change=='numeric_payload':batch['records'][0]['payload']['water_level_m']=1.2
                if change=='wrong_timezone':batch['records'][0]['timestamp']='2026-10-08T04:00:00Z'
                if change=='extra_field':batch['public_key']=self.public
                if change=='invalid_hash':batch['records'][0]['record_hash']='z'*64
                if change=='empty_records':batch['records']=[]
                self.assertEqual(self.post(batch)['reason_code'],'INVALID_BATCH_STRUCTURE')

    def test_cross_batch_time_must_increase(self):
        self.post(self.batch)
        second=self.make_batch(2,4,self.batch['records'][-1]['record_hash'])
        previous=second['records'][0]['previous_hash']
        for index,record in enumerate(second['records']):
            record['timestamp']=self.batch['records'][index]['timestamp']
            record['previous_hash']=previous
            record['record_hash']=record_hash(record)
            previous=record['record_hash']
        second['start_time']=second['records'][0]['timestamp']
        second['end_time']=second['records'][-1]['timestamp']
        second['merkle_root']=merkle_root([r['record_hash'] for r in second['records']])
        self.resign(second)
        self.assertEqual(self.post(second)['reason_code'],'INVALID_TIMESTAMP')

    def test_batch_number_must_increase(self):
        self.post(self.make_batch(number=2))
        next_batch=self.make_batch(1,4,storage.get_last_record('WL-001')['record_hash'])
        self.assertEqual(self.post(next_batch)['reason_code'],'INVALID_BATCH_SEQUENCE')

    def test_concurrent_duplicate_atomic(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.post(self.batch),range(2)))
        self.assertEqual(sorted(r['status'] for r in results),['accepted','duplicate'])
        self.assertEqual(storage.get_stats()['records'],3)


if __name__=='__main__':
    unittest.main()
