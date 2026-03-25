
import os
import sys
import time
import subprocess
import platform
import re
import socket
from zeroconf import ServiceInfo, Zeroconf

# ================= 核心工作区路径矫正 (打包脱线运行绝对锚点) =================
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(SCRIPT_DIR)
SYS_PLATFORM = platform.system()

# 强制绑定自身目录下的环境依赖 (自动兼容 Linux 与 Windows)
FFMPEG_EXE = os.path.join(SCRIPT_DIR, "ffmpeg.exe" if SYS_PLATFORM == "Windows" else "ffmpeg")
MTX_EXE = os.path.join(SCRIPT_DIR, "mediamtx.exe" if SYS_PLATFORM == "Windows" else "mediamtx")

if not os.path.exists(FFMPEG_EXE) or not os.path.exists(MTX_EXE):
    print(f"[致命错误] 找不到环境依赖组件！")
    print(f"请确保 【{FFMPEG_EXE}】 和 【{MTX_EXE}】 与本程序放在同一文件夹下！")
    sys.exit(1)
# =====================================================================

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception: return "127.0.0.1"

def setup_mtx_config():
    cfg_path = os.path.join(SCRIPT_DIR, "mediamtx.yml")
    if not os.path.exists(cfg_path):
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("paths:\n  all_others:\n")
    else:
        with open(cfg_path, "r", encoding="utf-8") as f: content = f.read()
        if "all_others:" not in content:
            if "\npaths:" in content or content.startswith("paths:"):
                content = content.replace("paths:", "paths:\n  all_others:")
            else: content += "\npaths:\n  all_others:\n"
            with open(cfg_path, "w", encoding="utf-8") as f: f.write(content)

def get_first_devices():
    v_dev, a_dev = None, None
    if SYS_PLATFORM == "Windows":
        cmd = [FFMPEG_EXE, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']
        result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        try: output_str = result.stderr.decode('gbk')
        except: output_str = result.stderr.decode('utf-8', errors='ignore')
            
        for line in output_str.split('\n'):
            if not v_dev and ("(video)" in line or "DirectShow video devices" in line):
                match = re.search(r'"([^"]+)"', line)
                if match and not match.group(1).startswith("@device"): v_dev = match.group(1)
            elif not a_dev and ("(audio)" in line or "DirectShow audio devices" in line):
                match = re.search(r'"([^"]+)"', line)
                if match and not match.group(1).startswith("@device"): a_dev = match.group(1)
    elif SYS_PLATFORM == "Linux": v_dev, a_dev = "/dev/video0", "default"
    return v_dev, a_dev

def main():
    print(f"=== 客户端监控节点启动 (平台: {SYS_PLATFORM}) ===")
    setup_mtx_config() 
    
    v_dev, a_dev = get_first_devices()
    if not v_dev:
        print("[错误] 没有检测到任何摄像头设备！")
        sys.exit(1)

    print("\n[管家] 将在当前屏幕上启动 MediaMTX 服务器日志...")
    mtx_process = subprocess.Popen([MTX_EXE])
    time.sleep(3) 
    if mtx_process.poll() is not None:
        print("[致命错误] MediaMTX 启动闪退！")
        sys.exit(1)
        
    print("\n[管家] 正在呼叫 FFmpeg 将高清画面注入网络...")
    stream_url = "rtsp://127.0.0.1:8554/live"
    
    cmd = [FFMPEG_EXE, '-y']
    cmd.extend(['-fflags', '+genpts+nobuffer+ignidx', '-use_wallclock_as_timestamps', '1'])
    
    if SYS_PLATFORM == "Windows":
        cmd.extend(['-audio_buffer_size', '20'])
        cmd.extend(['-video_size', '1280x720'])
        cmd.extend(['-f', 'dshow', '-i', f'video={v_dev}:audio={a_dev}'])
    else: 
        cmd.extend(['-video_size', '1280x720'])
        cmd.extend(['-f', 'v4l2', '-i', v_dev, '-f', 'alsa', '-i', a_dev])
        
    cmd.extend([
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-preset', 'superfast', 
        '-vf', r'scale=w=min(1280\,iw):h=min(720\,ih)',
        '-fps_mode', 'cfr', '-async', '1', 
        '-g', '15', '-sc_threshold', '0',
        '-crf', '28',        
        '-maxrate', '900k', '-bufsize', '1800k',
        '-r', '15',          
        '-c:a', 'aac', '-ar', '44100', '-b:a', '32k', '-ac', '1',
        '-f', 'rtsp', '-rtsp_transport', 'tcp', 
        stream_url
    ])

    ff_process = subprocess.Popen(cmd)
    
    time.sleep(3) 
    if ff_process.poll() is not None:
        mtx_process.terminate()
        sys.exit(1)

    local_ip = get_local_ip()
    hostname = socket.gethostname() 
    print("\n" + "="*50)
    print("✨ 网络流正在广播: rtsp://" + local_ip + ":8554/live ✨")

    print("[Zeroconf] 正在向局域网广播本节点的位置...")
    info = ServiceInfo(
        "_rtsp._tcp.local.",
        f"CameraNode_{hostname}._rtsp._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=8554,
        properties={'path': '/live'}, 
        server=f"{hostname}.local.",
    )

    zeroconf = Zeroconf()
    zeroconf.register_service(info)
    
    try:
        while True:
            if mtx_process.poll() is not None or ff_process.poll() is not None:
                print("\n进程意外退出！")
                break
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        print("\n[管家] 正在清理进程...")
        zeroconf.unregister_service(info) 
        zeroconf.close()
        if ff_process.poll() is None: ff_process.terminate()
        if mtx_process.poll() is None: mtx_process.terminate()

if __name__ == '__main__':
    main()
