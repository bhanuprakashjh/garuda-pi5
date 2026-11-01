"""Launcher behavior without starting flight or desktop processes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import lab_launcher as lab

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.stop_patch=patch.object(lab,'stop_managed')
        self.stopped=self.stop_patch.start()
        self.addCleanup(self.stop_patch.stop)

    def test_config_rejects_non_http_and_extra_fields(self):
        for url in ('file:///etc/passwd','http://host/path','http://user:pass@host','https://host'):
            with self.assertRaises(ValueError): lab.validate(dict(px4='',qgc='',actor='',data_root='',pi_url=url))
        with self.assertRaises(ValueError): lab.validate(dict(px4='',qgc='',actor='',data_root='',pi_url='http://pi',command='shell'))

    def test_saved_config_overrides_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text(json.dumps({'px4':'/custom/px4'}))
            with patch.object(lab,'CONFIG',path),patch.object(lab,'discover',return_value={'px4':'/detected','pi_url':'http://pi'}):
                self.assertEqual(lab.config()['px4'],'/custom/px4')

    def test_start_reuses_running_sim_and_qgc(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value={'pi_url':'http://pi'}),patch.object(lab,'missing',return_value=[]),patch.object(lab,'components',return_value={'sitl':True,'qgc':True,'gazebo':True}),patch.object(lab,'launch') as launch,patch.object(lab.subprocess,'run') as run:
            run.return_value.returncode=0
            lab.start();launch.assert_not_called();run.assert_called_once()

    def test_start_refuses_missing_dependencies(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value={}),patch.object(lab,'missing',return_value=['PX4']),patch.object(lab,'launch') as launch:
            with self.assertRaisesRegex(ValueError,'PX4'):lab.start()
            launch.assert_not_called()

    def test_sim_gate(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'0'}):
            with self.assertRaisesRegex(ValueError,'GARUDA_SIM'):lab.start()

    def test_no_duplicate_if_unmanaged_px4_exists(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value={'pi_url':'http://pi'}),patch.object(lab,'missing',return_value=[]),patch.object(lab,'components',return_value={'sitl':False,'qgc':True}),patch.object(lab,'processes',return_value=[['/build/px4','-i','3']]),patch.object(lab,'launch') as launch,patch.object(lab.subprocess,'run') as run:
            run.return_value.returncode=0
            with self.assertRaisesRegex(ValueError,'already exists'):lab.start()
            launch.assert_not_called()


    def test_start_attaches_to_existing_world(self):
        c=dict(pi_url='http://pi',px4='/PX4 with spaces',actor='/actor/walk.dae')
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value=c),patch.object(lab,'missing',return_value=[]),patch.object(lab,'components',return_value={'sitl':False,'qgc':True,'gazebo':True}),patch.object(lab,'processes',return_value=[]),patch.object(lab,'launch') as launch,patch.object(lab.subprocess,'run') as run:
            run.return_value.returncode=0
            lab.start()
            self.assertIn('--attach',launch.call_args.args[1])
            self.assertIn('/PX4 with spaces',launch.call_args.args[1])

    def test_home_available_without_controller(self):
        import threading, urllib.request, urllib.error
        server=lab.ThreadingHTTPServer(('127.0.0.1',0),lab.HTTP)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base) as response:
                self.assertIn(b'Switch to PX4 flight lab',response.read())
            request=urllib.request.Request(base+'/api/lab/start',data=b'{}',headers={'Content-Type':'application/json','Origin':'http://other.example'})
            with self.assertRaises(urllib.error.HTTPError) as result: urllib.request.urlopen(request)
            self.assertEqual(result.exception.code,403)
        finally: server.shutdown();server.server_close();thread.join()


    def test_new_sitl_clears_cached_controller_and_agent(self):
        c=dict(pi_url='http://pi',px4='/px4',actor='/actor/walk.dae')
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value=c),patch.object(lab,'missing',return_value=[]),patch.object(lab,'components',return_value={'sitl':False,'qgc':True,'gazebo':False}),patch.object(lab,'processes',return_value=[]),patch.object(lab,'launch'),patch.object(lab.subprocess,'run') as run:
            run.return_value.returncode=0;lab.start()
            self.assertEqual([c.args[0] for c in self.stopped.call_args_list],['dashboard','agent'])

    def test_missing_world_does_not_reuse_old_flight(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value={'pi_url':'http://pi'}),patch.object(lab,'missing',return_value=[]),patch.object(lab,'components',return_value={'sitl':True,'qgc':True,'gazebo':False}),patch.object(lab,'launch') as launch,patch.object(lab.subprocess,'run') as run:
            run.return_value.returncode=0
            with self.assertRaisesRegex(ValueError,'Reset simulation'): lab.start()
            launch.assert_not_called()

    def test_gazebo_exit_ends_supervision(self):
        import start_sitl
        from unittest.mock import Mock
        vehicle=Mock();vehicle.poll.return_value=None
        gazebo=Mock();gazebo.poll.return_value=0
        start_sitl.wait_for_simulation(vehicle,gazebo)
        vehicle.wait.assert_not_called()

    def test_reset_requires_sim_flag(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'0'}):
            with self.assertRaisesRegex(ValueError,'GARUDA_SIM'):lab.reset_simulation()
            self.stopped.assert_not_called()


    def test_reset_does_not_signal_reused_pid(self):
        with patch.dict(lab.os.environ,{'GARUDA_SIM':'1'}),patch.object(lab,'config',return_value={}),patch.object(lab,'missing',return_value=[]),patch.object(lab,'simulation_processes',return_value={999999:'old'}),patch.object(lab,'identity',return_value='new'),patch.object(lab.os,'kill') as kill,patch.object(lab,'start') as start:
            lab.reset_simulation()
            kill.assert_not_called();start.assert_called_once()
            self.assertEqual([c.args[0] for c in self.stopped.call_args_list],['dashboard','agent'])

if __name__=='__main__':unittest.main()
