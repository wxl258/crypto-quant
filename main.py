"""
量化交易 APP 主入口
启动时：① 后台启动 FastAPI 服务器  ② 前台显示 WebView

零基础用户：安装 APK → 打开 APP → 填 API Key → 开始使用
"""
import threading
import time
import sys
import os

# 将 crypto_quant 加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crypto_quant'))

from kivy.app import App
from kivy.core.window import Window

# ========== 第一步：启动 FastAPI 服务器 ==========

def start_server():
    """在后台线程启动量化系统服务器"""
    import logging
    logging.basicConfig(level=logging.WARNING)

    import uvicorn
    # 动态导入，避免循环引用
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "crypto_quant_main",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "crypto_quant", "main.py")
    )
    crypto_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crypto_main)
    uvicorn.run(crypto_main.app, host="127.0.0.1", port=8000, log_level="warning")

# ========== 第二步：Kivy WebView 界面 ==========

class QuantApp(App):
    def build(self):
        Window.clearcolor = (0.13, 0.14, 0.23, 1)  # 深色背景

        # 后台启动 FastAPI
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()

        # 等待服务器就绪（最多等20秒）
        import urllib.request
        ready = False
        for i in range(40):
            try:
                resp = urllib.request.urlopen(
                    "http://127.0.0.1:8000/health", timeout=1
                )
                if resp.status == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)

        # 创建 WebView
        try:
            # Android 环境
            from android.webkit import WebView, WebViewClient

            class SimpleClient(WebViewClient):
                def onPageFinished(self, view, url):
                    pass  # 页面加载完成

            wv = WebView()
            wv.getSettings().setJavaScriptEnabled(True)
            wv.getSettings().setDomStorageEnabled(True)
            wv.getSettings().setAllowFileAccess(True)
            wv.getSettings().setUseWideViewPort(True)
            wv.getSettings().setLoadWithOverviewMode(True)
            wv.setWebViewClient(SimpleClient())

            if ready:
                wv.loadUrl("http://127.0.0.1:8000")
            else:
                wv.loadData(
                    "<h2 style='color:white;text-align:center;margin-top:40%'>"
                    "服务器启动中...<br>请稍候或重启APP</h2>",
                    "text/html", "UTF-8"
                )

            return wv

        except ImportError:
            # 桌面端回退：提示用浏览器打开
            import webbrowser
            if ready:
                webbrowser.open("http://127.0.0.1:8000")
            from kivy.uix.label import Label
            return Label(
                text="量化系统已启动\n\n请在浏览器访问\nhttp://127.0.0.1:8000",
                font_size=20,
                color=(0.8, 0.9, 1, 1),
                halign="center",
                valign="middle",
            )


if __name__ == "__main__":
    QuantApp().run()
