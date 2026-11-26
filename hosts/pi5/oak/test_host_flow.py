from host_flow import TripleSelector,LatestJob,Trace

def packet(seq,t,arrival=0):return dict(seq=seq,device_ns=t,host_ns=t,dequeue_ns=arrival)

def test_wait_for_late_mono_and_no_reuse():
 s=TripleSelector(skew_ns=5,wait_ns=50)
 s.add('rgb',packet(1,100));s.add('left',packet(1,100))
 assert s.select(10) is None
 s.add('right',packet(1,102));assert s.select(11)['rgb']['seq']==1
 assert s.select(12) is None
 s.add('rgb',packet(2,110,20));assert s.select(21) is None
 assert s.select(71) is None and s.stats['skew_rejections']==1

def test_latest_complete_skips_and_worker_overwrite():
 s=TripleSelector()
 for seq in (1,2):
  for k in ('rgb','left','right'):s.add(k,packet(seq,seq*66_000_000))
 assert s.select()['rgb']['seq']==2 and s.stats['selection_skips']==1
 q=LatestJob();q.put(1);q.put(2)
 assert q.take()==2 and q.overwrites==1
 q.close();assert q.take() is None

def test_trace_detects_missing_history():
 t=Trace(2)
 for i in range(4):t.add('test',value=i)
 assert t.snapshot(0)['overrun']
 assert [r['id'] for r in t.snapshot(1)['records']]==[2,3]

def test_consumed_history_is_not_counted_as_dropped():
 s=TripleSelector(capacity=2)
 for seq in range(10):
  for k in s.frames:s.add(k,packet(seq,seq*100_000_000))
  assert s.select()['rgb']['seq']==seq
 assert s.stats['history_overwrites']==0

def test_hailo_admission_fails_closed():
 import pytest
 from types import SimpleNamespace
 from host_flow import require_exclusive_hailo
 with pytest.raises(RuntimeError):require_exclusive_hailo({})
 with pytest.raises(RuntimeError):require_exclusive_hailo({'GARUDA_EXCLUSIVE_STEREO':'1'},lambda *a,**kw:SimpleNamespace(returncode=0,stdout='active'))
 require_exclusive_hailo({'GARUDA_EXCLUSIVE_STEREO':'1'},lambda *a,**kw:SimpleNamespace(returncode=0,stdout='inactive'))


def test_preview_rgb_offset_does_not_relax_stereo_mates():
    def packet(seq,ts):return dict(seq=seq,device_ns=ts,dequeue_ns=0)
    s=TripleSelector(skew_ns=5,rgb_skew_ns=33,wait_ns=50)
    for name,ts in [('rgb',100),('left',123),('right',124)]:s.add(name,packet(1,ts))
    assert s.select(now=10) is not None
    s=TripleSelector(skew_ns=5,rgb_skew_ns=33,wait_ns=50)
    for name,ts in [('rgb',100),('left',123),('right',130)]:s.add(name,packet(1,ts))
    assert s.select(now=10) is None
    s=TripleSelector(skew_ns=5,rgb_skew_ns=33,wait_ns=50)
    for name,ts in [('rgb',100),('left',140),('right',141)]:s.add(name,packet(1,ts))
    assert s.select(now=10) is None


def test_default_selector_still_requires_close_rgb():
    s=TripleSelector(skew_ns=5,wait_ns=50)
    for name,ts in [('rgb',100),('left',123),('right',124)]:
        s.add(name,dict(seq=1,device_ns=ts,dequeue_ns=0))
    assert s.select(now=10) is None


def ring_packet(seq,device_ms,host_ms=None):
    return dict(seq=seq,device_ns=int(device_ms*1e6),host_ns=int((device_ms+5000 if host_ms is None else host_ms)*1e6))

def test_frame_ring_finds_the_pair_for_a_colour_frame():
    from host_flow import FrameRing
    r=FrameRing()
    for i in range(5):
        r.add('rgb',ring_packet(i,i*66.7));r.add('left',ring_packet(i,i*66.7+10));r.add('right',ring_packet(i,i*66.7+10.01))
    triple,reason=r.find(int((2*66.7+5000)*1e6)+400)
    assert reason is None and (triple['rgb']['seq'],triple['left']['seq'],triple['right']['seq'])==(2,2,2)

def test_frame_ring_refuses_unknown_frames_and_wide_timing():
    from host_flow import FrameRing
    r=FrameRing()
    assert r.find(1)==(None,'frame_not_in_history')
    r.add('rgb',ring_packet(0,0))
    assert r.find(int(5000e6)+5000)==(None,'frame_not_in_history')
    assert r.find(int(5000e6))==(None,'no_mono_frames')
    r.add('left',ring_packet(0,10));r.add('right',ring_packet(0,16))
    assert r.find(int(5000e6))==(None,'left_right_outside_limit')
    r=FrameRing();r.add('rgb',ring_packet(0,0));r.add('left',ring_packet(0,40));r.add('right',ring_packet(0,40))
    assert r.find(int(5000e6))==(None,'rgb_outside_limit')

def test_frame_ring_forgets_old_frames():
    from host_flow import FrameRing
    r=FrameRing(capacity=3)
    for i in range(6):r.add('rgb',ring_packet(i,i*66.7))
    assert r.find(int(5000e6))==(None,'frame_not_in_history')
