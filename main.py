import json
import requests
import os
import hashlib


def get_file_sha1(file_path):
    """获取文件的SHA1哈希值"""
    sha1_hash = hashlib.sha1()
    
    with open(file_path, 'rb') as f:
        # 分块读取，避免内存占用过大
        for chunk in iter(lambda: f.read(4096), b''):
            sha1_hash.update(chunk)
    
    return sha1_hash.hexdigest()


PATH_LAUNCHERMETA = './launchermeta' # launchermeta.mojang.com
PATH_PISTONMETA   = './piston-meta'  # piston-meta.mojang.com
PATH_LIBRARIES    = './libraries'    # libraries.minecraft.net

def write_file(path, content, mode, encoding='utf-8'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if 'b' in mode:
        with open(path, mode) as f:
            f.write(content)
    else:
        with open(path, mode, encoding=encoding) as f:
            f.write(content)

class ProgressBar:
    def __init__(self, task_name: str, current: int=0, minimum: int=0, maximum: int=100):
        self.task_name = task_name
        self.current = current
        self.minimum = minimum
        self.maximum = maximum

        # Make dirs
        os.makedirs(PATH_LAUNCHERMETA, exist_ok=True)
        os.makedirs(PATH_PISTONMETA, exist_ok=True)

    def reset(self, task_name: str=None, current: int=None, minimum: int=None, maximum: int=None):
        self.task_name = task_name if task_name is not None else self.task_name
        self.current = current if current is not None else self.current
        self.minimum = minimum if minimum is not None else self.minimum
        self.maximum = maximum if maximum is not None else self.maximum
    
    def add(self, delta=1):
        self.current += delta
        
    def get_percentage(self):
        if self.minimum >= self.maximum:
            return 0.0
        
        percent = (self.current - self.minimum) / (self.maximum - self.minimum) * 100
        return percent
    
    def draw(self):
        print(f'\r{self.task_name}: {self.get_percentage():.1f}%', end='', flush=True)


class McMirror:
    def __init__(self, override_manifest_urls: list=False, override_manifest: dict=False):
        # Session
        self.session = requests.Session()

        # Manifest Url
        if override_manifest_urls:
            self.manifest_urls = override_manifest_urls
        else:
            self.manifest_urls = ['https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json','https://launchermeta.mojang.com/mc/game/version_manifest_v2.json']
        

        # Get Manifest
        if override_manifest:
            self.manifest = override_manifest
        else:
            self.manifest = self.get_manifest()

        # init ProgressBar
        self.progbar = ProgressBar('Waiting')
        

    def download(self, url, to):
        try:
            response = self.session.get(url)
            response.raise_for_status()
            write_file(to, response.content, 'wb')
        except requests.RequestException as e:
            print(f"Failed to fetch from {url}: {e}")

    def get_manifest(self):
        for url in self.manifest_urls:
            try:
                response = self.session.get(url)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                print(f"Failed to fetch manifest from {url}: {e}")

    def download_version_jsons(self):
        self.progbar.reset('Download Version JSONs', 0, 0, len(self.manifest.get('versions', [])))
        for ver in self.manifest.get('versions', []):
            url = ver['url']
            to = ver['url'].replace('https://piston-meta.mojang.com', PATH_PISTONMETA)

            # Check if exists
            if os.path.exists(to) and get_file_sha1(to) == ver.get('sha1', ''):
                print('\rSkipped for already exists')
                self.progbar.add()
                self.progbar.draw()
                continue
            
            self.download(url, to)
            
            self.progbar.add()
            self.progbar.draw()


    def download_all(self):
        # Download version_manifest
        write_file(PATH_LAUNCHERMETA+'/mc/game/version_manifest.json', json.dumps(self.manifest), 'w')
        
        # Download Version JSONs
        self.download_version_jsons()


# Instance
if __name__ == "__main__":
    mirror = McMirror()
    mirror.download_all()