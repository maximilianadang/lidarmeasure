import json
import os
import signal
import subprocess
import time
import tempfile
import unittest
from pathlib import Path
import numpy as np
from stream_processing import NativeProcessor, T2Delays, Processor
from stream_capture import OverrunMonitor
from histogram_data import uniform_bin_indices


class ProcessingTests(unittest.TestCase):
    def test_native_handoff_preserves_events_across_batches_and_flushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'blocks').mkdir(); (out/'logs').mkdir()
            p = dict(mode='T2', chunk_records=2, chunk_seconds=5)
            progress = dict(blocks=0, committed_records=0, batches=0, records=0,
                            elapsed_s=0, channel_counts=[0]*5, unreferenced_events=0)
            worker = NativeProcessor(out, {}, p, [1000000, 1], 800, progress)
            try:
                worker.process(np.array([90,100,120], dtype=np.uint64), np.array([1,0,1], dtype=np.uint8), False)
                result = worker.process(np.array([150,200,230], dtype=np.uint64), np.array([1,0,1], dtype=np.uint8), True)
                final = worker.flush()
                self.assertEqual((final['records'], final['unreferenced_events'], final['committed_records']), (3,1,3))
            finally: worker.close()
            with np.load(out/'blocks/00000000.npz') as block:
                np.testing.assert_array_equal(block['delay_ps'], [20,50])
            self.assertIsNotNone(worker.worker.poll())

    def test_stalled_worker_does_not_block_saving_or_hold_borrowed_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'blocks').mkdir(); (out/'logs').mkdir()
            progress = dict(blocks=0, committed_records=0, batches=0, records=0,
                            elapsed_s=0, channel_counts=[0]*5, unreferenced_events=0)
            worker = NativeProcessor(out, {}, dict(mode='T2', chunk_records=2), [1000000, 1], 80, progress)
            try:
                os.kill(worker.worker.pid, signal.SIGSTOP)
                os.waitpid(worker.worker.pid, os.WUNTRACED)
                times = np.array([100,120], dtype=np.uint64)
                channels = np.array([0,1], dtype=np.uint8)
                for i in range(20):
                    result = worker.process(times, channels, False)
                    times += 100
                self.assertEqual(result['raw_batches_saved'], 20)
                self.assertGreater(result['processing_pending_batches'], 0)
                with np.load(out/'raw-batches/00000000.npz') as saved:
                    np.testing.assert_array_equal(saved['packed'], [100,120])
                os.kill(worker.worker.pid, signal.SIGCONT)
                final = worker.flush()
                self.assertEqual(final['committed_records'], 20)
                self.assertEqual(final['processing_pending_batches'], 0)
            finally:
                if worker.worker.poll() is None: os.kill(worker.worker.pid, signal.SIGCONT)
                worker.close()
            self.assertFalse((out/'raw-batches').exists())

    def test_worker_crash_preserves_raw_batches_and_does_not_stop_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'blocks').mkdir(); (out/'logs').mkdir()
            progress = dict(blocks=0, committed_records=0, batches=0, records=0,
                            elapsed_s=0, channel_counts=[0]*5, unreferenced_events=0)
            worker = NativeProcessor(out, {}, dict(mode='T2'), [1000000, 1], 80, progress)
            try:
                worker.worker.kill(); worker.worker.wait()
                for i in range(3):
                    result = worker.process(np.array([100,120], dtype=np.uint64), np.array([0,1], dtype=np.uint8), False)
                self.assertIn('processing_error', result)
                self.assertEqual(result['raw_batches_saved'], 3)
                with self.assertRaisesRegex(RuntimeError, 'raw batches preserved'): worker.flush()
            finally: worker.close()
            self.assertEqual(len(list((out/'raw-batches').glob('*.npz'))), 3)

    def test_checkpoint_recovery_preserves_sync_and_avoids_duplicate_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'blocks').mkdir(); (out/'logs').mkdir()
            progress = dict(blocks=0, committed_records=0, batches=0, records=0,
                            elapsed_s=0, channel_counts=[0]*5, unreferenced_events=0)
            worker = NativeProcessor(out, {}, dict(mode='T2', chunk_records=10, chunk_seconds=0), [1000000, 1], 80, progress)
            try:
                worker.process(np.array([100,120], dtype=np.uint64), np.array([0,1], dtype=np.uint8), False)
                deadline = time.monotonic()+10
                while not (worker.spool/'checkpoint.json').exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue((worker.spool/'checkpoint.json').exists())
                worker.worker.kill(); worker.worker.wait()
                worker.process(np.array([150], dtype=np.uint64), np.array([1], dtype=np.uint8), True)
                worker.worker = subprocess.Popen(worker.worker.args, stdout=worker.log, stderr=worker.log)
                final = worker.flush()
                self.assertEqual(final['committed_records'], 2)
                with np.load(out/'blocks/00000001.npz') as data:
                    np.testing.assert_array_equal(data['delay_ps'], [50])
            finally: worker.close()

    def test_decoder_matches_reference_with_simultaneous_events(self):
        rng = np.random.default_rng(2)
        times = np.sort(rng.integers(0,10000,100000,dtype=np.uint64))
        channels = rng.integers(0,5,len(times),dtype=np.uint8)
        decoder = T2Delays()
        for t, c in zip(np.array_split(times,10),np.array_split(channels,10)):
            sync=t[c==0]
            if decoder.last_sync is not None: sync=np.r_[decoder.last_sync,sync].astype(np.uint64)
            photons=t[c!=0];idx=np.searchsorted(sync,photons,side='right')-1;valid=idx>=0
            elapsed,delay,ch=decoder.decode(t,c)
            np.testing.assert_array_equal(delay,photons[valid]-sync[idx[valid]])
            np.testing.assert_array_equal(ch,c[c!=0][valid])
        self.assertEqual(len(decoder.decode(times[:0],channels[:0])[0]),0)

    def test_uniform_bins_match_searchsorted_at_boundaries(self):
        edges=np.r_[np.arange(100)*.12,11.91]
        values=np.r_[edges,np.nextafter(edges,-np.inf),np.nextafter(edges,np.inf),[-100,100]]
        np.testing.assert_array_equal(uniform_bin_indices(values,edges),np.searchsorted(edges,values,side='right')-1)

    def test_overrun_monitor_detects_new_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'snapi').mkdir(); log=out/'snapi/test.log'
            log.write_text('healthy\n'); monitor=OverrunMonitor(out); monitor.check()
            with log.open('a') as f: f.write('WRN Unfold1 buffer overrun - clearing!\n')
            with self.assertRaisesRegex(RuntimeError,'events were dropped'): monitor.check()

    def test_raw_t2_overflows_sync_markers_and_channel_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'blocks').mkdir()
            progress=dict(blocks=0,committed_records=0,batches=0,records=0,elapsed_s=0,channel_counts=[0]*5,unreferenced_events=0)
            worker=Processor(out,{},dict(mode='T2',raw_t2=True,chunk_records=10),[1000000,1],80,progress)
            try:
                worker.process(np.array([0x8000000a,20,0xfe000002],dtype=np.uint32),np.array([],dtype=np.uint8),False)
                worker.process(np.array([5,0x82000007,0x80000008,0x02000009],dtype=np.uint32),np.array([],dtype=np.uint8),True)
                worker.flush()
            finally: worker.close()
            with np.load(out/'blocks/00000000.npz') as data:
                np.testing.assert_array_equal(data['channels'],[1,1,2])
                np.testing.assert_allclose(data['elapsed_s'],np.array([20,2*(1<<25)+5,2*(1<<25)+9])*80e-12)
                np.testing.assert_array_equal(data['delay_ps'],[800,(2*(1<<25)-5)*80,80])
