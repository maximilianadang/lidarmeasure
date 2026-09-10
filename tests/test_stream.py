import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
from stream_capture import stream_events
from plot_waterfall import stream_histogram


class StreamingTests(unittest.TestCase):
    def profile(self):
        return dict(duration_ms=1000, window_ms=100, max_records=100, poll_ms=10,
                    min_free_disk_gb=1, preview_max_columns=5, save_ptu=False, expected_bin_width_ps=80)

    def measurement(self):
        m=Mock()
        m.isFinished.side_effect=[False, True]
        m.getBlock.side_effect=[(np.array([1,2]),np.array([1,1])),(np.array([3]),np.array([1]))]
        m.dTime_T3.side_effect=lambda x: np.full(len(x),730)
        m.nSync_T3.side_effect=lambda x: x*1000000
        return m

    def test_final_drain_and_bounded_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);m=self.measurement();p=self.profile()
            with patch('stream_capture.time.sleep'),patch('stream_capture.shutil.disk_usage',return_value=Mock(free=10**12)):
                result=stream_events(Mock(deviceConfig={'Resolution':80}),m,{},p,out,[10000000,1])
            self.assertEqual(result['records'],3)
            self.assertEqual(m.getBlock.call_count,2)
            config=dict(channel=1,reference_distance_m=18.3,reference_delay_ns=58.4,delay_window_ns=[0,100])
            te,re,counts,weighted,total,window=stream_histogram(out,p,config)
            self.assertLessEqual(len(te)-1,5)
            self.assertEqual(counts.sum(),3)
            self.assertEqual(total,3)
            self.assertFalse(list((out/'blocks').glob('*.tmp')))

    def test_disk_reserve_stops_and_preserves_committed_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);m=self.measurement()
            with patch('stream_capture.shutil.disk_usage',side_effect=[Mock(free=10**12),Mock(free=0)]):
                with self.assertRaisesRegex(RuntimeError,'Disk reserve'):
                    stream_events(Mock(deviceConfig={'Resolution':80}),m,{},self.profile(),out,[10000000,1])
            self.assertEqual(len(list((out/'blocks').glob('*.npz'))),1)
            self.assertEqual(json.loads((out/'stream-progress.json').read_text())['status'],'failed')

    def test_signal_stops_then_drains(self):
        import signal
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);m=self.measurement()
            with patch('stream_capture.time.sleep',side_effect=lambda _: signal.raise_signal(signal.SIGINT)),patch('stream_capture.shutil.disk_usage',return_value=Mock(free=10**12)):
                result=stream_events(Mock(deviceConfig={'Resolution':80}),m,{},self.profile(),out,[10000000,1])
            self.assertTrue(result['stop_requested'])
            m.stopMeasure.assert_called_once()
            self.assertEqual(result['records'],3)

class T2DecoderTests(unittest.TestCase):
    def test_sync_reference_survives_block_boundaries(self):
        from stream_capture import T2Delays
        d=T2Delays()
        elapsed,delay,ch=d.decode(np.array([90,100,120],dtype=np.uint64),np.array([1,0,1]))
        np.testing.assert_array_equal(delay,[20])
        self.assertEqual(d.unreferenced,1)
        elapsed,delay,ch=d.decode(np.array([150,200,230],dtype=np.uint64),np.array([1,0,2]))
        np.testing.assert_array_equal(delay,[50,30])
        np.testing.assert_allclose(elapsed,[150e-12,230e-12])
        np.testing.assert_array_equal(ch,[1,2])

    def test_empty_and_sync_only_blocks(self):
        from stream_capture import T2Delays
        d=T2Delays()
        t=np.array([],dtype=np.uint64)
        self.assertEqual(len(d.decode(t,t)[0]),0)
        self.assertEqual(len(d.decode(np.array([100],dtype=np.uint64),np.array([0]))[0]),0)
        np.testing.assert_array_equal(d.decode(np.array([110],dtype=np.uint64),np.array([1]))[1],[10])
