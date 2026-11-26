"""Delivered-image calibration metadata; no SDK dependency or accuracy claim."""
import math


def validate_geometry(g, *, epoch, calibration_id, width, height):
    if not isinstance(g, dict):
        raise ValueError('geometry must be an object')
    expected = dict(schema_version=1, epoch=epoch, calibration_id=calibration_id,
                    width=width, height=height, distortion='none',
                    reference_frame='unverified', verified=False)
    if g.get('source') not in ('sdk-transformation-metadata','host-opencv-factory-calibration'):
        raise ValueError('unknown geometry source')
    for k, value in expected.items():
        if g.get(k) != value or type(g.get(k)) is not type(value):
            raise ValueError('geometry mismatch: '+k)
    for k in ('fx','fy','cx','cy'):
        v=g.get(k)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
            raise ValueError('nonfinite geometry '+k)
    if g['fx'] <= 0 or g['fy'] <= 0 or not 0 <= g['cx'] < width or not 0 <= g['cy'] < height:
        raise ValueError('invalid intrinsic matrix')
    return g


def from_transform(transform, epoch, calibration_id):
    w,h=transform.getSize()
    k=transform.getIntrinsicMatrix()
    if any(float(x) != 0 for x in transform.getDistortionCoefficients()):
        raise ValueError('delivered image still has distortion')
    g=dict(schema_version=1, epoch=epoch, calibration_id=calibration_id,
           width=w,height=h,fx=float(k[0][0]),fy=float(k[1][1]),cx=float(k[0][2]),cy=float(k[1][2]),
           distortion='none',source='sdk-transformation-metadata',reference_frame='unverified',verified=False)
    return validate_geometry(g,epoch=epoch,calibration_id=calibration_id,width=w,height=h)
