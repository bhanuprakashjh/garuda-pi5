// garuda_vio_live: Basalt stereo-inertial odometry fed from a pipe. EXPERIMENTAL, bench only.
//
// It owns no camera and no network socket. A feeder (services/garuda-vio/vio_feed.py) writes
// images and IMU samples to stdin; one JSON line per estimated state comes out on stdout.
// Nothing here talks to a flight controller.
//
// Input, little-endian, after the 6-byte magic "GVIO1\n":
//   'I'  int64 t_ns, double gyro[3] (rad/s), double accel[3] (m/s^2)
//   'F'  int64 t_ns, uint16 width, uint16 height, uint8 cameras, then cameras*width*height bytes
//        of 8-bit grey, camera 0 first. t_ns is on the IMU clock (the feeder has already added
//        the calibrated camera time offset; Basalt itself does not apply it).
//   'E'  end of input (end of file means the same)
// IMU samples must arrive in time order and before the frames they precede.
//
// Output lines:
//   {"type":"state","t_ns":..,"p":[x,y,z],"q":[x,y,z,w],"v":[..],"bg":[..],"ba":[..]}
//       pose of the IMU in the odometry frame (gravity-aligned, origin at start), velocity in
//       that frame, current gyro and accelerometer bias estimates.
//   {"type":"tracked","t_ns":..,"n":[left,right]}     only with --report-tracked 1
//   {"type":"end","frames_in":..,"frames_dropped":..,"imu_in":..,"states":..}
//
// Links against Basalt (BSD-3-Clause). Built by hosts/pi5/vio/build_basalt.sh.

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <tbb/concurrent_queue.h>
#include <tbb/global_control.h>

#include <CLI/CLI.hpp>

#include <basalt/calibration/calibration.hpp>
#include <basalt/io/dataset_io.h>
#include <basalt/optical_flow/optical_flow.h>
#include <basalt/serialization/headers_serialization.h>
#include <basalt/vi_estimator/vio_estimator.h>

namespace {

bool read_exact(void* dst, size_t n) { return std::fread(dst, 1, n, stdin) == n; }

template <class V>
void print3(const char* key, const V& v) {
  std::printf(",\"%s\":[%.9g,%.9g,%.9g]", key, double(v[0]), double(v[1]), double(v[2]));
}

}  // namespace

int main(int argc, char** argv) {
  std::string calib_path, config_path;
  int num_threads = 0;
  int max_backlog = 0;
  bool use_double = false, report_tracked = false, realtime = false;

  CLI::App app{"Basalt stereo-inertial odometry from a pipe (experimental)"};
  app.add_option("--cam-calib", calib_path, "Basalt calibration JSON")->required();
  app.add_option("--config-path", config_path, "Basalt VIO config JSON")->required();
  app.add_option("--num-threads", num_threads, "Thread limit (0 = library default)");
  app.add_option("--max-backlog", max_backlog,
                 "Drop an incoming frame when this many are already waiting for the front end "
                 "(0 = never drop: the feeder waits, for replay as fast as possible)");
  app.add_option("--realtime", realtime,
                 "Let the estimator skip optimisation work when it falls behind (live use)");
  app.add_option("--report-tracked", report_tracked, "Also report tracked point counts");
  app.add_option("--use-double", use_double, "Use double, not float");
  try {
    app.parse(argc, argv);
  } catch (const CLI::ParseError& e) {
    return app.exit(e);
  }

  std::unique_ptr<tbb::global_control> limit;
  if (num_threads > 0)
    limit = std::make_unique<tbb::global_control>(
        tbb::global_control::max_allowed_parallelism, num_threads);

  basalt::VioConfig config;
  config.load(config_path);
  config.vio_enforce_realtime = realtime;

  basalt::Calibration<double> calib;
  {
    std::ifstream is(calib_path, std::ios::binary);
    if (!is.is_open()) {
      std::cerr << "cannot open calibration " << calib_path << std::endl;
      return 2;
    }
    cereal::JSONInputArchive archive(is);
    archive(calib);
  }
  const size_t cameras = calib.intrinsics.size();

  char magic[6];
  if (!read_exact(magic, 6) || std::memcmp(magic, "GVIO1\n", 6) != 0) {
    std::cerr << "input does not start with GVIO1" << std::endl;
    return 2;
  }

  auto flow = basalt::OpticalFlowFactory::getOpticalFlow(config, calib);
  auto vio = basalt::VioEstimatorFactory::getVioEstimator(
      config, calib, basalt::constants::g, true, use_double);
  vio->initialize(Eigen::Vector3d::Zero(), Eigen::Vector3d::Zero());
  flow->output_queue = &vio->vision_data_queue;

  tbb::concurrent_bounded_queue<basalt::PoseVelBiasState<double>::Ptr> states;
  tbb::concurrent_bounded_queue<basalt::VioVisualizationData::Ptr> vis;
  vio->out_state_queue = &states;
  if (report_tracked) vio->out_vis_queue = &vis;

  std::atomic<uint64_t> n_states{0};
  std::thread state_thread([&]() {
    basalt::PoseVelBiasState<double>::Ptr s;
    while (true) {
      states.pop(s);
      if (!s) break;
      const auto& T = s->T_w_i;
      const auto q = T.unit_quaternion();
      flockfile(stdout);
      std::printf("{\"type\":\"state\",\"t_ns\":%lld", static_cast<long long>(s->t_ns));
      print3("p", T.translation());
      std::printf(",\"q\":[%.9g,%.9g,%.9g,%.9g]", q.x(), q.y(), q.z(), q.w());
      print3("v", s->vel_w_i);
      print3("bg", s->bias_gyro);
      print3("ba", s->bias_accel);
      std::printf("}\n");
      std::fflush(stdout);
      funlockfile(stdout);
      ++n_states;
    }
  });
  std::unique_ptr<std::thread> vis_thread;
  if (report_tracked)
    vis_thread = std::make_unique<std::thread>([&]() {
      basalt::VioVisualizationData::Ptr d;
      while (true) {
        vis.pop(d);
        if (!d) break;
        flockfile(stdout);
        std::printf("{\"type\":\"tracked\",\"t_ns\":%lld,\"n\":[", static_cast<long long>(d->t_ns));
        for (size_t i = 0; i < d->projections.size(); i++)
          std::printf("%s%zu", i ? "," : "", d->projections[i].size());
        std::printf("]}\n");
        std::fflush(stdout);
        funlockfile(stdout);
      }
    });

  uint64_t frames_in = 0, frames_dropped = 0, imu_in = 0;
  std::vector<uint8_t> grey;
  int status = 0;
  while (true) {
    char kind;
    if (!read_exact(&kind, 1) || kind == 'E') break;
    if (kind == 'I') {
      int64_t t_ns;
      double v[6];
      if (!read_exact(&t_ns, 8) || !read_exact(v, sizeof(v))) break;
      basalt::ImuData<double>::Ptr d(new basalt::ImuData<double>);
      d->t_ns = t_ns;
      d->gyro = Eigen::Vector3d(v[0], v[1], v[2]);
      d->accel = Eigen::Vector3d(v[3], v[4], v[5]);
      vio->imu_data_queue.push(d);
      ++imu_in;
    } else if (kind == 'F') {
      int64_t t_ns;
      uint16_t w, h;
      uint8_t n;
      if (!read_exact(&t_ns, 8) || !read_exact(&w, 2) || !read_exact(&h, 2) || !read_exact(&n, 1))
        break;
      if (n != cameras || w == 0 || h == 0) {
        std::cerr << "frame has " << int(n) << " cameras of " << w << "x" << h
                  << ", calibration has " << cameras << std::endl;
        status = 2;
        break;
      }
      const size_t pixels = size_t(w) * h;
      grey.resize(pixels * n);
      if (!read_exact(grey.data(), grey.size())) break;
      ++frames_in;
      if (max_backlog > 0 && flow->input_queue.size() >= max_backlog) {
        ++frames_dropped;  // the front end is behind: the newest frame is not queued
        continue;
      }
      basalt::OpticalFlowInput::Ptr in(new basalt::OpticalFlowInput);
      in->t_ns = t_ns;
      in->img_data.resize(n);
      for (size_t c = 0; c < n; c++) {
        in->img_data[c].img.reset(new basalt::ManagedImage<uint16_t>(w, h));
        const uint8_t* src = grey.data() + c * pixels;
        for (size_t y = 0; y < h; y++) {
          uint16_t* row = in->img_data[c].img->RowPtr(y);
          for (size_t x = 0; x < w; x++) row[x] = uint16_t(src[y * w + x]) << 8;
        }
      }
      flow->input_queue.push(in);
    } else {
      std::cerr << "unknown record type " << int(kind) << std::endl;
      status = 2;
      break;
    }
  }

  flow->input_queue.push(nullptr);
  vio->imu_data_queue.push(nullptr);
  vio->maybe_join();
  states.push(nullptr);
  state_thread.join();
  if (vis_thread) {
    vis.push(nullptr);
    vis_thread->join();
  }
  std::printf("{\"type\":\"end\",\"frames_in\":%llu,\"frames_dropped\":%llu,\"imu_in\":%llu,\"states\":%llu}\n",
              static_cast<unsigned long long>(frames_in), static_cast<unsigned long long>(frames_dropped),
              static_cast<unsigned long long>(imu_in), static_cast<unsigned long long>(n_states.load()));
  std::fflush(stdout);
  return status;
}
