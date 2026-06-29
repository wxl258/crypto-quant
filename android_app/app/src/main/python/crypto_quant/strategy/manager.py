"""
策略管理器 — 热插拔、外部加载、启用/禁用、版本管理
"""
import os
import sys
import json
import hashlib
import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from version import __version__

logger = logging.getLogger(__name__)

# 策略存储目录
STRATEGY_DIR = Path(__file__).parent / "custom"
STRATEGY_STATE_FILE = Path(__file__).parent / "strategy_state.json"


@dataclass
class StrategyMeta:
    """策略元数据"""
    name: str
    file_name: str           # 策略文件名，如 my_strategy.py
    class_name: str          # 策略类名，如 MyStrategy
    version: str = __version__
    author: str = ""
    description: str = ""
    enabled: bool = True
    source: str = "builtin"  # builtin / custom / url
    source_url: str = ""     # 外部下载URL
    sha256: str = ""         # 文件校验
    params: Dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class StrategyManager:
    """统一策略管理器"""
    
    def __init__(self):
        self._custom_dir = STRATEGY_DIR
        self._custom_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
    
    # ── 状态持久化 ──
    
    def _load_state(self) -> Dict:
        """加载策略状态"""
        if STRATEGY_STATE_FILE.exists():
            try:
                with open(STRATEGY_STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load strategy state file: {e}")
                pass
        return {"custom_strategies": {}, "disabled": []}
    
    def _save_state(self):
        """保存策略状态"""
        with open(STRATEGY_STATE_FILE, 'w') as f:
            json.dump(self._state, f, indent=2, default=str)
    
    # ── 策略发现 ──
    
    def discover_strategies(self) -> List[str]:
        """自动发现所有可用策略（内置 + 自定义）"""
        from strategy.base import StrategyRegistry
        
        # 触发所有懒加载策略
        for name in list(StrategyRegistry._lazy_modules.keys()):
            if name not in StrategyRegistry._strategies:
                try:
                    StrategyRegistry.get(name)
                except Exception:
                    pass
        builtin = set(StrategyRegistry._strategies.keys())
        
        # 自定义策略
        custom = []
        if self._custom_dir.exists():
            for f in self._custom_dir.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                custom.append(f.stem)
        
        return sorted(builtin) + sorted(custom)
    
    def load_custom_strategies(self) -> List[Tuple[str, bool, str]]:
        """加载所有自定义策略，返回 (name, success, message)"""
        results = []
        
        if not self._custom_dir.exists():
            return results
        
        for py_file in self._custom_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            
            name = py_file.stem
            # 检查是否被禁用
            if name in self._state.get("disabled", []):
                results.append((name, False, "已禁用"))
                continue
            
            success, msg = self._load_strategy_from_file(str(py_file))
            results.append((name, success, msg))
        
        return results
    
    def _load_strategy_from_file(self, filepath: str) -> Tuple[bool, str]:
        """从文件加载单个策略"""
        from strategy.base import Strategy, StrategyRegistry
        
        try:
            module_name = f"strategy.custom.{Path(filepath).stem}"
            
            # 如果已加载，先卸载
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                return False, "无法解析策略文件"
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # 查找 Strategy 子类
            strategy_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, Strategy) and 
                    attr is not Strategy and
                    attr.__module__ == module_name):
                    strategy_class = attr
                    break
            
            if strategy_class is None:
                return False, "未找到继承自 Strategy 的类"
            
            # 注册
            StrategyRegistry.register(Path(filepath).stem, strategy_class)
            logger.info(f"Loaded custom strategy: {Path(filepath).stem}")
            return True, f"加载成功: {strategy_class.__name__}"
            
        except Exception as e:
            logger.error(f"Failed to load strategy {filepath}: {e}")
            return False, str(e)
    
    # ── 外部下载 ──
    
    def download_strategy(self, url: str, expected_sha256: str = "") -> Tuple[bool, str]:
        """从URL下载策略文件并加载"""
        import requests
        
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                return False, f"下载失败: HTTP {resp.status_code}"
            
            content = resp.text
            
            # SHA256 校验
            if expected_sha256:
                actual_sha256 = hashlib.sha256(content.encode()).hexdigest()
                if actual_sha256 != expected_sha256:
                    return False, f"SHA256校验失败\n期望: {expected_sha256[:16]}...\n实际: {actual_sha256[:16]}..."
            
            # 从URL提取文件名
            filename = url.split("/")[-1]
            if not filename.endswith(".py"):
                filename = f"downloaded_{hashlib.md5(url.encode()).hexdigest()[:8]}.py"
            
            filepath = self._custom_dir / filename
            filepath.write_text(content, encoding="utf-8")
            
            # 尝试加载
            success, msg = self._load_strategy_from_file(str(filepath))
            if not success:
                filepath.unlink()  # 加载失败则删除
                return False, f"策略加载失败: {msg}"
            
            # 记录元数据
            sha256 = hashlib.sha256(content.encode()).hexdigest()
            self._state["custom_strategies"][filepath.stem] = {
                "source": "url",
                "source_url": url,
                "sha256": sha256,
                "version": __version__,
                "downloaded_at": str(__import__('datetime').datetime.now()),
            }
            self._save_state()
            
            return True, f"下载并加载成功: {filepath.stem}"
            
        except requests.RequestException as e:
            return False, f"网络错误: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"
    
    # ── 启用/禁用 ──
    
    def disable_strategy(self, name: str) -> Tuple[bool, str]:
        """禁用策略（不卸载，仅标记）"""
        from strategy.base import StrategyRegistry
        
        if name not in StrategyRegistry._strategies:
            return False, f"策略 '{name}' 不存在"
        
        if "disabled" not in self._state:
            self._state["disabled"] = []
        
        if name not in self._state["disabled"]:
            self._state["disabled"].append(name)
            self._save_state()
        
        return True, f"策略 '{name}' 已禁用（重启后生效）"
    
    def enable_strategy(self, name: str) -> Tuple[bool, str]:
        """启用策略"""
        if name in self._state.get("disabled", []):
            self._state["disabled"].remove(name)
            self._save_state()
        
        # 如果是自定义策略，尝试重新加载
        filepath = self._custom_dir / f"{name}.py"
        if filepath.exists():
            return self._load_strategy_from_file(str(filepath))
        
        return True, f"策略 '{name}' 已启用"
    
    # ── 删除 ──
    
    def delete_strategy(self, name: str) -> Tuple[bool, str]:
        """删除自定义策略"""
        from strategy.base import StrategyRegistry
        
        filepath = self._custom_dir / f"{name}.py"
        if not filepath.exists():
            return False, "只能删除自定义策略"
        
        # 从注册表移除
        if name in StrategyRegistry._strategies:
            del StrategyRegistry._strategies[name]
        
        # 删除文件
        filepath.unlink()
        
        # 清理状态
        self._state["custom_strategies"].pop(name, None)
        if name in self._state.get("disabled", []):
            self._state["disabled"].remove(name)
        self._save_state()
        
        # 卸载模块
        module_name = f"strategy.custom.{name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        return True, f"策略 '{name}' 已删除"
    
    # ── 热重载 ──
    
    def hot_reload_all(self) -> Tuple[List[str], List[str]]:
        """热重载所有策略（内置 + 自定义），返回 (成功列表, 错误列表)"""
        from strategy.base import StrategyRegistry
        import strategy as strategy_pkg
        
        errors = []
        loaded = []
        
        # 清除注册表
        StrategyRegistry._strategies.clear()
        
        # 重新加载内置策略模块
        builtin_modules = [
            "strategy.base", "strategy.dual_ma", "strategy.rsi_mean_reversion",
            "strategy.grid", "strategy.bollinger", "strategy.macd",
            "strategy.supertrend", "strategy.turtle", "strategy.portfolio",
            "strategy.ensembles", "strategy.adaptive",
            "strategy.trend_follower", "strategy.mean_reversion_v2",
            "strategy.regime_analyzer", "strategy.regime_adaptive",
            "strategy.smart_meta", "strategy.meta_strategy",
            "strategy.ultimate", "strategy.smart_follower",
            "strategy.mtf_strategy", "strategy.funding_arb",
            "strategy.features", "strategy.ai_strategy",
            "strategy.multi_agent_strategy",
        ]
        
        for mod_name in builtin_modules:
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
            except Exception as e:
                errors.append(f"reload {mod_name}: {e}")
        
        # 重新运行 __init__.py 的注册逻辑（现已覆盖所有内置策略）
        try:
            importlib.reload(strategy_pkg)
        except Exception as e:
            errors.append(f"reload strategy __init__: {e}")

        # 加载自定义策略
        custom_results = self.load_custom_strategies()
        for name, success, msg in custom_results:
            if success:
                loaded.append(name)
            else:
                errors.append(f"{name}: {msg}")
        
        return loaded, errors
    
    # ── 获取策略源码 ──
    
    def get_strategy_source(self, name: str) -> Optional[str]:
        """获取策略源码"""
        from strategy.base import StrategyRegistry
        
        # 检查是否是自定义策略
        filepath = self._custom_dir / f"{name}.py"
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        
        # 内置策略：尝试找到源文件
        strategy_cls = StrategyRegistry.get(name)
        if strategy_cls:
            try:
                source_file = sys.modules[strategy_cls.__module__].__file__
                if source_file:
                    return Path(source_file).read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read strategy source file for '{name}': {e}")
                pass
        return None
    
    def get_strategy_info(self, name: str) -> Optional[Dict]:
        """获取策略详细信息（含元数据）"""
        from strategy.base import StrategyRegistry
        
        cls = StrategyRegistry.get(name)
        if cls is None:
            return None
        
        info = {
            "name": name,
            "class_name": cls.__name__,
            "module": cls.__module__,
            "description": (cls.__doc__ or "").strip(),
            "parameters": cls.get_param_info() if hasattr(cls, 'get_param_info') else [],
            "enabled": name not in self._state.get("disabled", []),
            "source": "custom" if (self._custom_dir / f"{name}.py").exists() else "builtin",
        }
        
        # 附加自定义元数据
        if name in self._state.get("custom_strategies", {}):
            info.update(self._state["custom_strategies"][name])
        
        return info


# 全局单例
_strategy_manager: Optional[StrategyManager] = None

def get_strategy_manager() -> StrategyManager:
    global _strategy_manager
    if _strategy_manager is None:
        _strategy_manager = StrategyManager()
        # 启动时自动加载自定义策略
        _strategy_manager.load_custom_strategies()
    return _strategy_manager
