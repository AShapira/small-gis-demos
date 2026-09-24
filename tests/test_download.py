import tempfile,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rasterbench.fetch import verify_archive

class Downloads(unittest.TestCase):
    def test_pinned_downloader_resumes_partial_response(self):
        import copernicus_downloader as d
        payload=b'reproducible archive payload'*1024
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'product.zip'; path.with_suffix('.zip.part').write_bytes(payload[:123])
            p=SimpleNamespace(id='fixture',name='fixture.SAFE',tile_id='fixture',content_length=len(payload))
            store=d.StateStore(Path(td)/'state.db');store.ensure_download('fixture',p,path)
            class Response:
                status_code=206
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def iter_content(self,chunk_size):yield payload[123:]
            with patch.object(d.requests,'get',return_value=Response()) as get:
                d.download_product(SimpleNamespace(token=lambda:'test-only'),store,p,path)
                self.assertEqual(get.call_args.kwargs['headers']['Range'],'bytes=123-')
            self.assertEqual(path.read_bytes(),payload)
            self.assertFalse(path.with_suffix('.zip.part').exists());store.close()

    def test_archive_crc_and_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.zip'
            with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('RGB','unique-pixel-payload')
            good=p.read_bytes();self.assertEqual(verify_archive(p)['bytes'],len(good))
            damaged=good.replace(b'unique-pixel-payload',b'broken-pixel-payload')
            p.write_bytes(damaged)
            with self.assertRaises(ValueError):verify_archive(p)
            p.write_bytes(good[:-40])
            with self.assertRaises(zipfile.BadZipFile):verify_archive(p)
