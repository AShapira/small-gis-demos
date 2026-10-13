import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

from rasterbench.workload import preseed


class PreseedRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_retries_only_failed_tile_and_saves_evidence(self):
        calls={}
        async def request(url,width,height):
            calls[url]=calls.get(url,0)+1
            first_tile=parse_qs(urlsplit(url).query)['TILECOL']==['1']
            return {'ok':not (first_tile and calls[url]==1),'url':url}
        with tempfile.TemporaryDirectory() as td:
            evidence=Path(td)/'preseed.jsonl'
            failed=await preseed(request,[(4,1,2),(4,2,2)],'test','image/png',evidence,retry_delay=0)
            self.assertEqual(failed,1)
            self.assertEqual(sorted(calls.values()),[1,2])
            rows=[json.loads(line) for line in evidence.read_text().splitlines()]
            self.assertEqual(len(rows),3)
            self.assertEqual(sum(not r['ok'] for r in rows),1)

    async def test_persistent_failure_stops_before_measurement_and_keeps_attempts(self):
        async def request(url,width,height):
            return {'ok':False,'error':'HTTP 500'}
        with tempfile.TemporaryDirectory() as td:
            evidence=Path(td)/'preseed.jsonl'
            with self.assertRaisesRegex(RuntimeError,'after 3 attempts'):
                await preseed(request,[(4,1,2)],'test','image/png',evidence,retry_delay=0)
            rows=[json.loads(line) for line in evidence.read_text().splitlines()]
            self.assertEqual([r['attempt'] for r in rows],[1,2,3])
            self.assertTrue(all(r['error']=='HTTP 500' for r in rows))
