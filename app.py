import os
import re
import json
import webbrowser
from pathlib import Path
from datetime import datetime
from threading import Lock
from flask import Flask, request, jsonify, send_from_directory, render_template
import subprocess
from pydub import AudioSegment

app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = './downloads'
app.config['HISTORY_FILE'] = './download_history.json'
app.config['PROGRESS_FILE'] = './download_progress.json'

Path(app.config['DOWNLOAD_FOLDER']).mkdir(exist_ok=True, parents=True)

if not os.path.exists(app.config['HISTORY_FILE']):
    with open(app.config['HISTORY_FILE'], 'w') as f:
        json.dump([], f)

progress_lock = Lock()

class DownloadProgress:
    def __init__(self):
        self.percentage = 0.0
        self.speed = "0 KB/s"
        self.eta = "0:00"
        self.stage = "preparing"
        self.last_updated = datetime.now()

    def update(self, data):
        with progress_lock:
            try:
                if 'stage' in data:
                    self.stage = data['stage']
                
                if 'percentage' in data:
                    percent_str = data.get('percentage', '0%').replace('%', '').strip()
                    self.percentage = round(float(percent_str), 1) if percent_str else 0.0
                
                if 'speed' in data:
                    speed = data.get('speed', '0 KB/s')
                    self.speed = speed.replace('i', '')
                
                if 'eta' in data:
                    eta = data.get('eta', '0:00')
                    self.eta = str(eta).replace('NA', '00:00').replace('unknown', '00:00')
                
                self.last_updated = datetime.now()
                self.save_to_file()
            except Exception as e:
                print(f"Progress güncelleme hatası: {str(e)}")

    def save_to_file(self):
        data = {
            "percentage": self.percentage,
            "speed": self.speed,
            "eta": self.eta,
            "stage": self.stage,
            "timestamp": datetime.now().isoformat()
        }
        try:
            with open(app.config['PROGRESS_FILE'], 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Progress kaydetme hatası: {str(e)}")

    def clear(self):
        with progress_lock:
            self.percentage = 0.0
            self.speed = "0 KB/s"
            self.eta = "0:00"
            self.stage = "preparing"
            try:
                if os.path.exists(app.config['PROGRESS_FILE']):
                    os.remove(app.config['PROGRESS_FILE'])
            except Exception as e:
                print(f"Progress dosyası silinemedi: {str(e)}")

progress_tracker = DownloadProgress()

def convert_to_mp3(input_path, output_path):
    """Pydub ile MP3 dönüşümü"""
    try:
        audio = AudioSegment.from_file(input_path)
        audio.export(output_path, format="mp3", bitrate="320k")
        return True
    except Exception as e:
        print(f"Dönüşüm hatası: {str(e)}")
        return False

def get_latest_file():
    try:
        files = os.listdir(app.config['DOWNLOAD_FOLDER'])
        if not files:
            return None
        return max(
            [os.path.join(app.config['DOWNLOAD_FOLDER'], f) for f in files],
            key=os.path.getctime
        )
    except Exception as e:
        print(f"Dosya bulunamadı: {str(e)}")
        return None

def update_download_history(entry):
    try:
        history = []
        if os.path.exists(app.config['HISTORY_FILE']):
            with open(app.config['HISTORY_FILE'], 'r') as f:
                history = json.load(f)
        
        history.append(entry)
        
        with open(app.config['HISTORY_FILE'], 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Geçmiş güncellenirken hata: {str(e)}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_info', methods=['POST'])
def get_video_info():
    data = request.json
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'Lütfen bir URL girin'}), 400

    youtube_regex = (
        r'(https?://)?(www\.|music\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )

    if not re.match(youtube_regex, url):
        return jsonify({'error': 'Geçersiz YouTube URL'}), 400

    try:
        result = subprocess.run(
            ['yt-dlp', '--dump-json', '--skip-download', url],
            capture_output=True,
            encoding='utf-8',
            text=True,
            timeout=30,
            check=True
        )
        info = json.loads(result.stdout)
        
        formats = []
        for f in info.get('formats', []):
            if f.get('vcodec') != 'none':
                formats.append({
                    'type': 'video',
                    'id': f['format_id'],
                    'ext': f['ext'],
                    'resolution': f.get('resolution', 'Bilinmiyor'),
                    'filesize': f.get('filesize_approx', f.get('filesize', 0)),
                    'vcodec': f.get('vcodec', 'Bilinmiyor').split('.')[0],
                    'acodec': f.get('acodec', 'Bilinmiyor').split('.')[0],
                    'has_audio': f.get('acodec') != 'none'
                })

        formats.append({
            'type': 'audio',
            'id': 'mp3',
            'ext': 'mp3',
            'resolution': 'Ses',
            'filesize': info.get('filesize', 0),
            'vcodec': 'MP3',
            'acodec': 'MP3',
            'has_audio': True
        })

        return jsonify({
            'title': info.get('title', ''),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'formats': formats,
            'tags': info.get('tags', [])[:5]
        })
        
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f"YT-DLP hatası: {e.stderr}"}), 400
    except Exception as e:
        return jsonify({'error': f"Beklenmeyen hata: {str(e)}"}), 500
    
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                              'favicon.ico', mimetype='image/vnd.microsoft.icon')
    
@app.route('/download', methods=['POST'])
def download_media():
    data = request.json
    url = data['url']
    selected_format = data.get('format', '')
    
    base_options = [
        'yt-dlp',
        '-o', f"{app.config['DOWNLOAD_FOLDER']}/%(title)s.%(ext)s",
        '--newline',
        '--progress',
        '--progress-template', r'[PROGRESS]{"percentage":"%(progress._percent_str)s","speed":"%(progress._speed_str)s","eta":"%(progress._eta_str)s"}',
        '--no-simulate',
        '--ignore-errors',
        #'--restrict-filenames',
        url
    ]

    try:
        progress_tracker.clear()
        output_lines = []
        
        if selected_format == 'mp3':
            temp_options = base_options + [
                '-x',
                '--audio-format', 'best',
                '--no-embed-thumbnail'
            ]
            
            process = subprocess.Popen(
                temp_options,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding='utf-8',
                bufsize=1
            )

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    output_lines.append(line)
                    if '[PROGRESS]' in line:
                        try:
                            progress_str = line.split('[PROGRESS]')[1].strip()
                            progress = json.loads(progress_str)
                            progress['stage'] = 'downloading'
                            progress_tracker.update(progress)
                        except:
                            pass

            if process.returncode != 0:
                raise Exception("Audio indirme hatası")

            temp_file = get_latest_file()
            if not temp_file:
                raise Exception("Geçici dosya bulunamadı")

            progress_tracker.update({
                'stage': 'processing',
                'percentage': '100%'
            })
            
            output_file = os.path.splitext(temp_file)[0] + '.mp3'
            if not convert_to_mp3(temp_file, output_file):
                raise Exception("MP3 dönüşümü başarısız")

            os.remove(temp_file)
            final_file = output_file

        else:
            process = subprocess.Popen(
                base_options + ['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding='utf-8',
                bufsize=1
            )

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    output_lines.append(line)
                    if '[PROGRESS]' in line:
                        try:
                            progress_str = line.split('[PROGRESS]')[1].strip()
                            progress = json.loads(progress_str)
                            progress['stage'] = 'downloading'
                            progress_tracker.update(progress)
                        except:
                            pass

            if process.returncode != 0:
                raise Exception("Video indirme hatası")

            final_file = get_latest_file()

        if not final_file or not os.path.exists(final_file):
            raise Exception("Dosya oluşturulamadı")

        history_entry = {
            'url': url,
            'format': selected_format,
            'type': 'audio' if selected_format == 'mp3' else 'video',
            'filename': os.path.basename(final_file),
            'timestamp': datetime.now().isoformat(),
            'status': 'success'
        }
        update_download_history(history_entry)
        
        return jsonify({
            'status': 'success',
            'filename': os.path.basename(final_file)
        })

    except Exception as e:
        progress_tracker.clear()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/progress')
def get_progress():
    try:
        if not os.path.exists(app.config['PROGRESS_FILE']):
            return jsonify({
                "percentage": 0,
                "speed": "0 KB/s",
                "eta": "0:00",
                "stage": "preparing"
            })
        
        with open(app.config['PROGRESS_FILE'], 'r') as f:
            try:
                return jsonify(json.load(f))
            except json.JSONDecodeError:
                return jsonify({
                    "percentage": 0,
                    "speed": "0 KB/s",
                    "eta": "0:00",
                    "stage": "preparing"
                })
    except Exception as e:
        return jsonify({
            "percentage": 0,
            "speed": "0 KB/s",
            "eta": "0:00",
            "stage": "preparing"
        })

@app.route('/check_file')
def check_file():
    filename = request.args.get('filename')
    file_path = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    return jsonify(os.path.exists(file_path))

@app.route('/history')
def get_history():
    try:
        with open(app.config['HISTORY_FILE'], 'r') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify([])

@app.route('/downloads/<path:filename>')
def download_file(filename):
    return send_from_directory(app.config['DOWNLOAD_FOLDER'], filename)

if __name__ == '__main__':
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        webbrowser.open_new("http://127.0.0.1:5000")
    app.run(debug=False)