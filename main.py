import json
import requests
import os
import hashlib
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

def get_file_sha1(file_path):
    """获取文件的SHA1哈希值"""
    sha1_hash = hashlib.sha1()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha1_hash.update(chunk)
        return sha1_hash.hexdigest()
    except FileNotFoundError:
        return ""

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
        self._lock = threading.Lock()  # 引入线程锁，防止多线程打印错乱

    def reset(self, task_name: str=None, current: int=None, minimum: int=None, maximum: int=None):
        with self._lock:
            self.task_name = task_name if task_name is not None else self.task_name
            self.current = current if current is not None else self.current
            self.minimum = minimum if minimum is not None else self.minimum
            self.maximum = maximum if maximum is not None else self.maximum
    
    def add(self, delta=1):
        with self._lock:
            self.current += delta
        
    def get_percentage(self):
        if self.minimum >= self.maximum:
            return 0.0
        return (self.current - self.minimum) / (self.maximum - self.minimum) * 100
    
    def draw(self):
        with self._lock:
            # 使用 \r 实现单行刷新
            print(f'\r{self.task_name}: {self.get_percentage():.1f}% ({self.current}/{self.maximum})', end='', flush=True)


class McMirror:
    def __init__(self, override_manifest_urls: list=None, override_manifest: dict=None, max_workers: int=16, max_retries: int=3):
        """
        :param max_workers: 线程池最大并发数（可扩展性）
        :param max_retries: 单个文件下载失败的最大重试次数（可靠性）
        """
        self.session = requests.Session()
        self.version_json_paths = []
        self.max_workers = max_workers
        self.max_retries = max_retries

        self.tryBMCLAPI = True  # 是否启用 BMCLAPI 镜像加速

        if override_manifest_urls:
            self.manifest_urls = override_manifest_urls
        else:
            self.manifest_urls = [
                # 'https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json',
                'https://launchermeta.mojang.com/mc/game/version_manifest_v2.json'
            ]
        
        self.manifest = override_manifest if override_manifest else self.get_manifest()
        self.progbar = ProgressBar('Waiting')

        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0'})
        
    def url_to_local_path(self, url: str) -> str:
        parsed_url = urlparse(url)
        host = parsed_url.netloc
        folder_name = host.split('.')[0] if '.' in host else host
        local_path = os.path.join('.', folder_name, parsed_url.path.lstrip('/'))
        return os.path.normpath(local_path)

    def download_single_file(self, url: str, to: str, sha1: str="", timeout=(5, 30), tryBMCLAPI=False) -> bool:
        """
        下载单个核心原子操作：包含校验、重试机制
        """
        if tryBMCLAPI:
            replacer = {
                'https://libraries.minecraft.net/': 'https://bmclapi2.bangbang93.com/maven/',
                # 'https://maven.neoforged.net/releases/': 'https://bmclapi2.bangbang93.com/maven/',
                # 'https://maven.minecraftforge.net/': 'https://bmclapi2.bangbang93.com/maven/',
                # 'https://files.minecraftforge.net/maven': 'https://bmclapi2.bangbang93.com/maven/',
                # 'https://meta.fabricmc.net': 'https://bmclapi2.bangbang93.com/fabric-meta',
                'https://launchermeta.mojang.com/': 'https://bmclapi2.bangbang93.com/',
                'https://launcher.mojang.com/': 'https://bmclapi2.bangbang93.com/',
                'http://resources.download.minecraft.net/': 'https://bmclapi2.bangbang93.com/assets/',
                # 'http://dl.liteloader.com/versions/versions.json': 'https://bmclapi.bangbang93.com/maven/com/mumfrey/liteloader/versions.json',
                # 'https://authlib-injector.yushi.moe': 'https://bmclapi2.bangbang93.com/mirrors/authlib-injector',
                # 'https://maven.fabricmc.net/': 'https://bmclapi2.bangbang93.com/maven/',
            } # God bless BMCLAPI
            for k, v in replacer.items():
                if url.startswith(k):
                    url = url.replace(k, v, 1)
                    break
        if os.path.exists(to) and sha1 != "" and get_file_sha1(to) == sha1:
            return True  # 校验通过，跳过下载

        # 下载逻辑
        try:
            # 使用流式下载以节省内存
            with self.session.get(url, timeout=timeout, stream=True) as r:
                r.raise_for_status()
                os.makedirs(os.path.dirname(to), exist_ok=True)
                with open(to, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            # 校验
            return get_file_sha1(to) == sha1 if sha1 else True
        except Exception as e:
            return False

    def _execute_thread_pool(self, tasks: list, task_name: str):
        """
        通用的线程池执行器：可扩展、可靠地分发下载任务
        tasks 格式: [(url, to, sha1), ...]
        """
        if not tasks:
            return

        self.progbar.reset(task_name, 0, 0, len(tasks))
        self.progbar.draw()

        # 使用 ThreadPoolExecutor 实现并发下载
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务到线程池
            future_to_task = {
                executor.submit(self.download_single_file, url, to, sha1, tryBMCLAPI=self.tryBMCLAPI): (url, to) 
                for url, to, sha1 in tasks
            }
            
            # 动态监听任务完成情况，更新进度条
            for future in as_completed(future_to_task):
                self.progbar.add()
                self.progbar.draw()

    def get_manifest(self):
        for url in self.manifest_urls:
            try:
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                print(f"Failed to fetch manifest from {url}: {e}")
        raise RuntimeError("All manifest URLs failed to fetch.")

    def download_version_jsons(self):
        versions = self.manifest.get('versions', [])
        tasks = []
        
        for ver in versions:
            url = ver['url']
            to = self.url_to_local_path(url)
            self.version_json_paths.append(to)
            tasks.append((url, to, ver.get('sha1', '')))
            
        # 交付线程池执行
        self._execute_thread_pool(tasks, 'Download Version JSONs')

    def download_baseFiles(self):
        tasks = []
        
        # 1. 预解析所有需要下载的文件，生成任务队列
        for path in self.version_json_paths:
            if not os.path.exists(path):
                continue
                
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    version_data = json.load(f)
                except json.JSONDecodeError:
                    continue
                    
                # 收集 assetIndex
                asset_index = version_data.get('assetIndex')
                if asset_index:
                    tasks.append((asset_index['url'], self.url_to_local_path(asset_index['url']), asset_index.get('sha1', '')))
            
                # 收集 client/server 等 base 文件
                for i in version_data.get('downloads', {}).values():
                    tasks.append((i['url'], self.url_to_local_path(i['url']), i.get('sha1', '')))

        # 2. 交付线程池并发下载
        self._execute_thread_pool(tasks, 'Download Base Files')

    def download_all(self):
        manifest_save_url = self.manifest_urls[0]
        to = self.url_to_local_path(manifest_save_url)
        
        write_file(to, json.dumps(self.manifest, indent=4), 'w')
        print(f"Saved manifest to: {to}")
        
        self.download_version_jsons()
        print() # 换行
        
        self.download_baseFiles()
        print("\nAll done!")


if __name__ == "__main__":
    # 你可以通过调整 max_workers 来改变并发数（推荐 16 ~ 32，根据带宽决定）
    # 通过调整 max_retries 来决定失败重试次数
    mirror = McMirror(max_workers=32, max_retries=3)
    mirror.download_all()