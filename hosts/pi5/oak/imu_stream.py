"""Bounded IMU history. Counters and timestamps are evidence, not inferred loss counts."""
from collections import deque

class IMUHistory:
    def __init__(self, epoch, capacity=4096):
        self.epoch=epoch;self.rows=deque(maxlen=capacity);self.next_id=0;self.stats={}

    def add(self, kind, report, arrival_ns):
        row=dict(id=self.next_id,stream=kind,seq=int(report.sequence),
                 device_s=report.getTimestampDevice().total_seconds(),
                 host_s=report.getTimestamp().total_seconds(),arrival_s=arrival_ns/1e9,
                 x=float(report.x),y=float(report.y),z=float(report.z))
        self.next_id+=1;self.rows.append(row)
        old=self.stats.get(kind)
        self.stats[kind]=dict(samples=(old['samples']+1 if old else 1),
                             first_host_s=old['first_host_s'] if old else row['host_s'],
                             last_host_s=row['host_s'],last_seq=row['seq'])

    def snapshot(self, after=-1, epoch=None):
        reset=epoch!=self.epoch
        cursor=-1 if reset else after
        missed=bool(self.rows and cursor>=0 and cursor<self.rows[0]['id']-1)
        return dict(epoch=self.epoch,cursor_overrun=missed,epoch_changed=reset,
                    next_id=self.next_id,samples=[r for r in self.rows if r['id']>cursor])

    def health(self,now_ns):
        out={}
        for kind,s in self.stats.items():
            elapsed=s['last_host_s']-s['first_host_s'];age=now_ns/1e9-s['last_host_s']
            out[kind]=dict(s,received_hz=(s['samples']-1)/elapsed if elapsed>0 else None,
                           age_s=age,stale=not 0<=age<1)
        return out
