"""Fetch the Apache-2.0 OpenCV Zoo YOLOX evaluation model, verify LFS SHA256."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request

REV='48b10fa505aeb51fbd127fc60a5c660040e823b7'
ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'.runtime/models/yolox'

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    base=f'https://raw.githubusercontent.com/opencv/opencv_zoo/{REV}/models/object_detection_yolox/'
    name='object_detection_yolox_2022nov.onnx'
    pointer=urllib.request.urlopen(base+name,timeout=30).read().decode()
    sha=re.search(r'oid sha256:([a-f0-9]{64})',pointer).group(1)
    path=DEST/name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        data=urllib.request.urlopen(f'https://media.githubusercontent.com/media/opencv/opencv_zoo/{REV}/models/object_detection_yolox/{name}',timeout=90).read()
        if hashlib.sha256(data).hexdigest()!=sha:raise RuntimeError('Model SHA mismatch')
        path.write_bytes(data)
    (DEST/'LICENSE').write_bytes(urllib.request.urlopen(base+'LICENSE',timeout=30).read())
    (DEST/'manifest.json').write_text(json.dumps(dict(revision=REV,sha256=sha,license='Apache-2.0'),indent=2))
    print(path,sha)

if __name__=='__main__':main()
