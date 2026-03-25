
import os
import sys
import time
import threading
import subprocess
import platform
import re
import json
import socket
import tkinter as tk
from tkinter import filedialog
from flask import Flask, render_template_string, redirect, url_for, request
from zeroconf import ServiceBrowser, Zeroconf
from werkzeug.serving import make_server

# ================= 核心工作区路径矫正 (打包脱线运行绝对锚点) =================
if getattr(sys, 'frozen', False):
    # 如果是被 PyInstaller 打包后的运行状态
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    # 纯 Python 脚本运行状态
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(SCRIPT_DIR)
SYS_PLATFORM = platform.system()

# 强制绑定自身目录下的环境依赖
FFMPEG_EXE = os.path.join(SCRIPT_DIR, "ffmpeg.exe" if SYS_PLATFORM == "Windows" else "ffmpeg")
if not os.path.exists(FFMPEG_EXE):
    print(f"[致命错误] 找不到环境依赖: {FFMPEG_EXE}")
    print("请确保将 ffmpeg 放在与本程序一致的目录下！")
    sys.exit(1)
# =====================================================================

app = Flask(__name__)
PORT = 5000                      

CONFIG_FILE = "config_server.json"
CONFIG = {}
DISCOVERED_NET_CAMS = {}
ACTIVE_PROCESSES = {}
LOCAL_V_DEVICES, LOCAL_A_DEVICES = [], []

class CameraListener:
    def remove_service(self, zeroconf, type, name):
        cam_id = name.split('.')[0]
        if cam_id in DISCOVERED_NET_CAMS:
            print(f"[🚨 雷达警报] 网络节点已掉线: {cam_id}")
            del DISCOVERED_NET_CAMS[cam_id]

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port
            path = info.properties.get(b'path', b'/live').decode('utf-8')
            rtsp_url = f"rtsp://{ip}:{port}{path}"
            cam_id = name.split('.')[0]
            
            DISCOVERED_NET_CAMS[cam_id] = {
                "id": cam_id, "ip": ip, "rtsp_url": rtsp_url
            }
            print(f"[📡 雷达提示] 发现局域网摄像头上线: {cam_id} ({ip})")

    def update_service(self, zeroconf, type, name): pass

def init_cam_config(cam_id):
    if cam_id not in CONFIG:
        CONFIG[cam_id] = {
            "alias_name": "", 
            "save_dir": os.path.join(SCRIPT_DIR, "recordings", cam_id),
            "segment_minutes": 60,
            "max_age_days": 7,
            "v_dev": "", "a_dev": "" 
        }

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                CONFIG = json.load(f)
        except Exception: pass
    init_cam_config("local_cam")

def save_config():
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=4)
    except Exception: pass

def get_local_devices():
    global LOCAL_V_DEVICES, LOCAL_A_DEVICES
    v_list, a_list = [], []
    if SYS_PLATFORM == "Windows":
        # 换用自带的完整路径 FFMPEG_EXE
        cmd = [FFMPEG_EXE, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']
        result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            output_str = result.stderr.decode('gbk')
        except UnicodeDecodeError:
            output_str = result.stderr.decode('utf-8', errors='ignore')
            
        for line in output_str.split('\n'):
            if "(video)" in line or "DirectShow video devices" in line:
                match = re.search(r'"([^"]+)"', line)
                if match and not match.group(1).startswith("@device"):
                    if match.group(1) not in v_list: v_list.append(match.group(1))
            elif "(audio)" in line or "DirectShow audio devices" in line:
                match = re.search(r'"([^"]+)"', line)
                if match and not match.group(1).startswith("@device"):
                    if match.group(1) not in a_list: a_list.append(match.group(1))
    elif SYS_PLATFORM == "Linux":  
        v_list, a_list = ["/dev/video0"], ["default"]
    LOCAL_V_DEVICES, LOCAL_A_DEVICES = v_list, a_list

def cleanup_old_files():
    while True:
        now = time.time()
        for cam_id, cam_cfg in CONFIG.items():
            target_dir = cam_cfg.get("save_dir", "")
            max_age_sec = cam_cfg.get("max_age_days", 7) * 24 * 3600
            
            if target_dir and os.path.exists(target_dir):
                for filename in os.listdir(target_dir):
                    if filename.startswith("Record_") and filename.endswith(".mp4"):
                        filepath = os.path.join(target_dir, filename)
                        if os.path.isfile(filepath):
                            if os.stat(filepath).st_mtime < (now - max_age_sec):
                                try: os.remove(filepath)
                                except Exception: pass
        time.sleep(3600)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>企业级 NVR 监控矩阵平台</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #eaeef3; margin: 0; padding: 15px;}
        h1 { text-align: center; color: #2c3e50; margin-top: 10px; margin-bottom: 25px; font-size: 24px;}
        .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; max-width: 1400px; margin: 0 auto; }
        .card { background: white; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); padding: 20px; border-top: 5px solid #bdc3c7;}
        .card.active { border-top-color: #27ae60; box-shadow: 0 4px 15px rgba(39, 174, 96, 0.2); }
        .card-header { font-size: 18px; font-weight: bold; margin-bottom: 15px; color: #34495e; border-bottom: 2px solid #ecf0f1; padding-bottom: 10px; display: flex; justify-content: space-between; align-items: center;}
        .badge { font-size: 11px; padding: 5px 8px; border-radius: 4px; color: white; background: #95a5a6; white-space: nowrap;}
        .badge.recording { background: #27ae60; animation: pulse 2s infinite;}
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
        .form-group { margin-bottom: 12px; }
        label { display: block; font-size: 13px; color: #7f8c8d; margin-bottom: 5px; font-weight: bold;}
        input[type=number], input[type=text], select { width: 100%; padding: 10px; border: 1px solid #dcdde1; border-radius: 6px; box-sizing: border-box; font-size: 14px;}
        .folder-group { display: flex; gap: 8px; }
        .folder-group input { margin-bottom: 0; background-color: #f1f2f6; color: #576574; flex: 1;}
        .btn { border: none; padding: 12px 15px; cursor: pointer; border-radius: 6px; font-size: 15px; color: white; transition: all 0.2s; font-weight: bold; width: 100%; box-sizing: border-box; text-align: center; text-decoration: none; display: inline-block;}
        .btn-pick { background: #3498db; width: auto; white-space: nowrap;}
        .btn-save { background: #f39c12; margin-top: 5px;}
        .btn-start { background: #2ecc71; margin-top: 15px;}
        .btn-start:hover { background: #27ae60; }
        .btn-stop { background: #e74c3c; margin-top: 15px;}
        .btn-stop:hover { background: #c0392b; }
        .refresh-bar { text-align: center; margin-bottom: 20px;}
        .btn-refresh { background: #34495e; width: auto; min-width: 250px;}
        .info-text { font-size: 12px; color: #95a5a6; margin-top: 8px; text-align: center;}
    </style>
</head>
<body>
    <h1>🏢 NVR 监控指挥中心</h1>
    <div class="refresh-bar">
        <a href="/" class="btn btn-refresh">🔄 刷新状态与局域网节点</a>
    </div>

    <div class="grid-container">

        <!-- 本地硬件摄像头 -->
        <div class="card {{ 'active' if active_procs.get('local_cam') else '' }}">
            <div class="card-header">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">💻 本地主控机位</span>
                <span class="badge {{ 'recording' if active_procs.get('local_cam') else '' }}">
                    {{ '▶ 录像中' if active_procs.get('local_cam') else '⏸ 待命中' }}
                </span>
            </div>
            
            <form action="/save_config/local_cam" method="post">
                <div class="form-group">
                    <label>📂 独立保存路径:</label>
                    <div class="folder-group">
                        <input type="text" name="save_dir" value="{{ conf.local_cam.get('save_dir','') }}" readonly>
                        <a href="/pick_dir/local_cam" class="btn btn-pick">浏览</a>
                    </div>
                </div>
                <div class="form-group" style="display: flex; gap: 10px;">
                    <div style="flex:1;">
                        <label>⏱ 切分(分钟):</label>
                        <input type="number" name="segment_minutes" value="{{ conf.local_cam.get('segment_minutes', 60) }}" min="1">
                    </div>
                    <div style="flex:1;">
                        <label>🗑 保留(天数):</label>
                        <input type="number" name="max_age_days" value="{{ conf.local_cam.get('max_age_days', 7) }}" min="1">
                    </div>
                </div>
                <button type="submit" class="btn btn-save">💾 保存本机策略</button>
            </form>

            <hr style="border: 0; border-top: 1px solid #eee; margin: 15px 0;">

            {% if not active_procs.get('local_cam') %}
                <form action="/start/local_cam" method="post">
                    <div class="form-group">
                        <label>📷 本地视频源:</label>
                        <select name="v_dev">
                            {% for dev in local_v %}
                                <option value="{{ dev }}" {% if dev == conf.local_cam.v_dev %}selected{% endif %}>{{ dev }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>🎤 本地音频源:</label>
                        <select name="a_dev">
                            {% for dev in local_a %}
                                <option value="{{ dev }}" {% if dev == conf.local_cam.a_dev %}selected{% endif %}>{{ dev }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <button type="submit" class="btn btn-start">▶ 启动本机录像</button>
                </form>
            {% else %}
                <br>
                <a href="/stop/local_cam" class="btn btn-stop">⏹ 终止本机录像</a>
                <div class="info-text">正在极速编码中，系统已接管调度。</div>
            {% endif %}
        </div>

        <!-- 局域网发现的网络摄像头 -->
        {% for cam_id, net_cam in net_cams.items() %}
        <div class="card {{ 'active' if active_procs.get(cam_id) else '' }}">
            <div class="card-header">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%;">
                    🌐 {{ conf[cam_id].get('alias_name') or '网络节点' }}
                </span>
                <span class="badge {{ 'recording' if active_procs.get(cam_id) else '' }}">
                    {{ '▶ 录像中' if active_procs.get(cam_id) else '⏸ 待命中' }}
                </span>
            </div>
            
            <form action="/save_config/{{ cam_id }}" method="post">
                <div class="form-group">
                    <label>🏷 节点备注名称 (选填):</label>
                    <input type="text" name="alias_name" value="{{ conf[cam_id].get('alias_name', '') }}" placeholder="例如：客厅摄像头">
                </div>
                <div class="form-group">
                    <label>📂 节点独立接收存放地:</label>
                    <div class="folder-group">
                        <input type="text" name="save_dir" value="{{ conf[cam_id].get('save_dir','') }}" readonly>
                        <a href="/pick_dir/{{ cam_id }}" class="btn btn-pick">浏览</a>
                    </div>
                </div>
                <div class="form-group" style="display: flex; gap: 10px;">
                    <div style="flex:1;">
                        <label>⏱ 切分(分钟):</label>
                        <input type="number" name="segment_minutes" value="{{ conf[cam_id].get('segment_minutes', 60) }}" min="1">
                    </div>
                    <div style="flex:1;">
                        <label>🗑 保留(天数):</label>
                        <input type="number" name="max_age_days" value="{{ conf[cam_id].get('max_age_days', 7) }}" min="1">
                    </div>
                </div>
                <button type="submit" class="btn btn-save">💾 保存节点策略</button>
            </form>

            <hr style="border: 0; border-top: 1px solid #eee; margin: 15px 0;">

            {% if not active_procs.get(cam_id) %}
                <form action="/start/{{ cam_id }}" method="post">
                    <input type="hidden" name="rtsp_url" value="{{ net_cam.rtsp_url }}">
                    <div class="info-text" style="color:#2c3e50; font-size:13px; text-align:left; margin-bottom:10px;">
                        <strong>流来源：</strong> {{ net_cam.ip }}<br>
                        <strong>特征：</strong> 高清免解压无损流拷直写
                    </div>
                    <button type="submit" class="btn btn-start">▶ [无损流拷] 开启录像</button>
                </form>
            {% else %}
                <br>
                <a href="/stop/{{ cam_id }}" class="btn btn-stop">⏹ 终止网络流接收</a>
                <div class="info-text">中控机挂载拉流中，硬盘直写保护中。</div>
            {% endif %}
        </div>
        {% endfor %}

    </div>
</body>
</html>
"""

@app.route('/')
def index():
    load_config()
    dead_cams = [cid for cid, proc in ACTIVE_PROCESSES.items() if proc.poll() is not None]
    for cid in dead_cams: del ACTIVE_PROCESSES[cid]
    for cid in DISCOVERED_NET_CAMS: init_cam_config(cid)
    return render_template_string(HTML_TEMPLATE,
                                  conf=CONFIG,
                                  local_v=LOCAL_V_DEVICES, local_a=LOCAL_A_DEVICES,
                                  net_cams=DISCOVERED_NET_CAMS,
                                  active_procs=ACTIVE_PROCESSES)

@app.route('/pick_dir/<cam_id>')
def pick_dir(cam_id):
    init_cam_config(cam_id)
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder_path = filedialog.askdirectory(initialdir=CONFIG[cam_id].get('save_dir'))
        root.destroy()
        if folder_path:
            CONFIG[cam_id]['save_dir'] = os.path.normpath(folder_path)
            save_config()
    except Exception: pass
    return redirect(url_for('index'))

@app.route('/save_config/<cam_id>', methods=['POST'])
def save_config_route(cam_id):
    init_cam_config(cam_id)
    try:
        alias = request.form.get('alias_name')
        if alias is not None: CONFIG[cam_id]["alias_name"] = alias.strip()
            
        save_dir = request.form.get('save_dir')
        if save_dir: CONFIG[cam_id]["save_dir"] = save_dir
            
        seg_str = request.form.get('segment_minutes')
        if seg_str and seg_str.isdigit(): CONFIG[cam_id]["segment_minutes"] = int(seg_str)
            
        max_str = request.form.get('max_age_days')
        if max_str and max_str.isdigit(): CONFIG[cam_id]["max_age_days"] = int(max_str)
            
        save_config()
    except Exception: pass
    return redirect(url_for('index'))

@app.route('/start/<cam_id>', methods=['POST'])
def start(cam_id):
    init_cam_config(cam_id)
    target_dir = CONFIG[cam_id].get("save_dir")
    os.makedirs(target_dir, exist_ok=True)
    
    if cam_id not in ACTIVE_PROCESSES or ACTIVE_PROCESSES[cam_id].poll() is not None:
        cmd = [FFMPEG_EXE, '-y']
        segment_seconds = str(CONFIG[cam_id].get("segment_minutes", 60) * 60)
        
        identifier = CONFIG[cam_id].get("alias_name")
        if not identifier:
            identifier = DISCOVERED_NET_CAMS[cam_id]["ip"] if cam_id in DISCOVERED_NET_CAMS else "Local_Camera"
            
        filename_format = os.path.join(target_dir, f'Record_{identifier}_%Y-%m-%d_%H-%M-%S.mp4')
        
        if cam_id == "local_cam":
            v_dev = request.form.get('v_dev')
            a_dev = request.form.get('a_dev')
            if not v_dev or not a_dev: return redirect(url_for('index'))
            
            CONFIG[cam_id]["v_dev"], CONFIG[cam_id]["a_dev"] = v_dev, a_dev
            save_config()
            
            cmd.extend(['-use_wallclock_as_timestamps', '1'])
            if SYS_PLATFORM == "Windows": 
                cmd.extend(['-video_size', '1280x720'])
                cmd.extend(['-f', 'dshow', '-i', f'video={v_dev}:audio={a_dev}'])
            else: 
                cmd.extend(['-video_size', '1280x720'])
                cmd.extend(['-f', 'v4l2', '-i', v_dev])
            
            cmd.extend([
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-preset', 'superfast', '-crf', '28', '-r', '15',
                '-fps_mode', 'cfr', '-g', '15', '-sc_threshold', '0',
                '-maxrate', '900k', '-bufsize', '1800k',
                '-force_key_frames', f'expr:gte(t,n_forced*{segment_seconds})',
                '-max_muxing_queue_size', '1024', 
                '-vf', r'scale=w=min(1280\,iw):h=min(720\,ih)',
                '-c:a', 'aac', '-b:a', '32k', '-ac', '1',
                '-f', 'segment', '-segment_time', segment_seconds,
                '-reset_timestamps', '1', '-strftime', '1',
                filename_format
            ])
            
        else:
            rtsp_url = request.form.get('rtsp_url')
            if not rtsp_url: return redirect(url_for('index'))
            
            cmd.extend([
                '-use_wallclock_as_timestamps', '1', 
                '-fflags', '+genpts+nobuffer',       
                '-rtsp_transport', 'tcp',    
                '-i', rtsp_url,              
                '-c', 'copy',                
                '-f', 'segment',
                '-segment_time', segment_seconds,
                '-reset_timestamps', '1', '-strftime', '1',
                filename_format
            ])
            
        ACTIVE_PROCESSES[cam_id] = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    return redirect(url_for('index'))

@app.route('/stop/<cam_id>')
def stop(cam_id):
    if cam_id in ACTIVE_PROCESSES and ACTIVE_PROCESSES[cam_id].poll() is None:
        try: ACTIVE_PROCESSES[cam_id].communicate(b'q', timeout=5)
        except subprocess.TimeoutExpired: ACTIVE_PROCESSES[cam_id].kill()
        del ACTIVE_PROCESSES[cam_id]
    return redirect(url_for('index'))

if __name__ == '__main__':
    def start_ipv4():
        try: make_server('0.0.0.0', PORT, app).serve_forever()
        except: pass

    print("=== NVR 中控多路系统启动 ===")
    load_config()
    get_local_devices()
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    
    zeroconf = Zeroconf()
    listener = CameraListener()
    browser = ServiceBrowser(zeroconf, "_rtsp._tcp.local.", listener)
    
    t4 = threading.Thread(target=start_ipv4, daemon=True)
    t4.start()

    print(f"\n[系统] 请用手机或电脑IP访问面板，端口: {PORT}")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        zeroconf.close()
