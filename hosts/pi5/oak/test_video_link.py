import pytest
from video_link import command,nals,nal_type

def test_annex_b_parameter_and_idr_types():
    data=b'\0\0\0\1\x67abc\0\0\1\x68def\0\0\0\1\x65ghi'
    assert [nal_type(n,'h264') for n in nals(data)]==[7,8,5]
    data=b'\0\0\1\x40\x01abc\0\0\1\x26\x01def'
    assert [nal_type(n,'h265') for n in nals(data)]==[32,19]

@pytest.mark.parametrize('host',['example.com; rm','host name','256.0.0.1'])
def test_no_shell_or_unresolved_target(host):
    with pytest.raises(ValueError): command('h265',host,5600)

def test_relay_only_no_encoding():
    cmd=command('h265','127.0.0.1',5600)
    assert 'h265parse' in cmd and 'rtph265pay' in cmd
    assert not any('enc' in x for x in cmd)
