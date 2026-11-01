"""Generate only the ROS interfaces used by the demo from the exact local PX4 source.

PX4 message definitions are BSD-3-Clause; keep their source license with generated package.
"""
from pathlib import Path
import argparse
import shutil
import subprocess

NAMES = ('OffboardControlMode', 'TrajectorySetpoint', 'VehicleCommand',
         'VehicleCommandAck', 'VehicleOdometry', 'VehicleStatus', 'VehicleLandDetected')

def prepare(px4, output):
    output.mkdir(parents=True, exist_ok=True)
    (output/'msg').mkdir(exist_ok=True)
    for name in NAMES:
        candidates = [px4/'msg'/f'{name}.msg', px4/'msg/versioned'/f'{name}.msg']
        source = next(p for p in candidates if p.exists())
        shutil.copy2(source, output/'msg'/source.name)
    shutil.copy2(px4/'LICENSE', output/'LICENSE')
    (output/'package.xml').write_text('''<?xml version="1.0"?>
<package format="3"><name>px4_msgs</name><version>0.1.0</version>
<description>Exact local PX4 message subset for isolated demo</description>
<maintainer email="bench@example.invalid">Garuda bench</maintainer><license>BSD-3-Clause</license>
<buildtool_depend>ament_cmake</buildtool_depend><build_depend>rosidl_default_generators</build_depend>
<exec_depend>rosidl_default_runtime</exec_depend><member_of_group>rosidl_interface_packages</member_of_group>
<export><build_type>ament_cmake</build_type></export></package>''')
    files='\n'.join(f'  "msg/{n}.msg"' for n in NAMES)
    (output/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.16)
project(px4_msgs)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME}
'''+files+''')
ament_export_dependencies(rosidl_default_runtime)
ament_package()
''')
    (output/'PX4_REVISION').write_text(subprocess.check_output(['git','-C',str(px4),'rev-parse','HEAD'],text=True))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('px4',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();prepare(a.px4,a.output)
