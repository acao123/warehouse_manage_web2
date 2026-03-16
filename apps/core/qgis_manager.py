# -*- coding: utf-8 -*-
"""
QGIS 单例管理器

提供线程安全的 QGIS 实例管理，确保：
1. 全局只有一个 QgsApplication 实例
2. 提供资源获取和释放的上下文管理器
3. 异常时不会导致 Web 进程崩溃
"""

import gc
import logging
import os
import threading
import weakref
from contextlib import contextmanager
from typing import Optional, Any

logger = logging.getLogger('report.qgis')

# QGIS 相关模块延迟导入标志
_qgis_imported = False
QgsApplication = None
QgsProject = None


def _ensure_qgis_imports():
    """延迟导入 QGIS 模块，避免在导入时就初始化"""
    global _qgis_imported, QgsApplication, QgsProject
    if not _qgis_imported:
        from qgis.core import QgsApplication as _QgsApp
        from qgis.core import QgsProject as _QgsProj
        QgsApplication = _QgsApp
        QgsProject = _QgsProj
        _qgis_imported = True


class QGISManager:
    """
    QGIS 单例管理器

    使用方法：
        with QGISManager.get_instance().acquire(task_id=123) as (app, project):
            # 使用 app 和 project
            pass
        # 自动释放资源

    特性：
    - 线程安全的单例模式
    - 延迟初始化 QGIS
    - 自动资源清理
    - 异常隔离
    """

    _instance: Optional['QGISManager'] = None
    _lock = threading.Lock()
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._qgs_app: Optional[Any] = None
        self._resource_lock = threading.RLock()  # 可重入锁，支持同线程多次获取
        self._active_sessions = 0
        self._initialized = True
        self._layers_registry = weakref.WeakSet()  # 弱引用跟踪图层

        logger.info('QGISManager 单例已创建')

    @classmethod
    def get_instance(cls) -> 'QGISManager':
        """获取 QGIS 管理器单例"""
        return cls()

    def _init_qgis(self) -> bool:
        """
        初始化 QGIS 应用（仅首次调用时执行）

        返回:
            bool: 初始化是否成功
        """
        with self._init_lock:
            _ensure_qgis_imports()

            # 检查是否已有实例
            if QgsApplication.instance() is not None:
                self._qgs_app = QgsApplication.instance()
                logger.info('复用已存在的 QgsApplication 实例')
                return True

            if self._qgs_app is not None:
                return True

            try:
                # 设置 QGIS 路径
                qgis_prefix_path = os.environ.get(
                    'QGIS_PREFIX_PATH',
                    r'D:\App\dev\QGIS3.40.15\apps\qgis-ltr'
                )

                QgsApplication.setPrefixPath(qgis_prefix_path, True)
                self._qgs_app = QgsApplication([], False)
                self._qgs_app.initQgis()

                logger.info('QGIS 应用初始化成功，路径: %s', qgis_prefix_path)
                return True

            except Exception as exc:
                logger.error('QGIS 初始化失败: %s', exc, exc_info=True)
                self._qgs_app = None
                return False

    @contextmanager
    def acquire(self, task_id: Optional[int] = None):
        """
        获取 QGIS 资源的上下文管理器

        用法：
            with manager.acquire(task_id=123) as (app, project):
                # 使用资源
                pass

        参数:
            task_id: 任务 ID（用于日志追踪）

        Yields:
            tuple: (QgsApplication 实例, QgsProject 实例)

        Raises:
            RuntimeError: QGIS 初始化失败时
        """
        log_prefix = f'[任务 {task_id}]' if task_id else '[QGIS]'

        self._resource_lock.acquire()
        try:
            self._active_sessions += 1
            logger.debug('%s 获取 QGIS 资源，当前会话数: %d', log_prefix, self._active_sessions)

            # 确保 QGIS 已初始化
            if not self._init_qgis():
                raise RuntimeError('QGIS 初始化失败，无法执行任务')

            _ensure_qgis_imports()
            project = QgsProject.instance()

            # 清理上一次的项目状态
            project.clear()

            yield self._qgs_app, project

        except Exception as exc:
            logger.error('%s QGIS 资源使用过程中发生异常: %s', log_prefix, exc, exc_info=True)
            raise

        finally:
            try:
                # 清理本次使用的资源
                self._cleanup_session(task_id)
            except Exception as cleanup_exc:
                logger.warning('%s 资源清理时发生异常: %s', log_prefix, cleanup_exc)
            finally:
                self._active_sessions -= 1
                logger.debug('%s 释放 QGIS 资源，剩余会话数: %d', log_prefix, self._active_sessions)
                self._resource_lock.release()

    def cleanup_session(self, task_id: Optional[int] = None):
        """
        清理单次会话的 QGIS 资源（公共接口）。

        在外部代码（如任务执行器）需要手动触发资源清理时调用，
        例如在 ``finally`` 块中确保任务结束后释放图层和布局。

        参数:
            task_id: 任务 ID（用于日志追踪）
        """
        self._cleanup_session(task_id)

    def _cleanup_session(self, task_id: Optional[int] = None):
        """清理单次会话的资源"""
        log_prefix = f'[任务 {task_id}]' if task_id else '[QGIS]'

        try:
            _ensure_qgis_imports()
            project = QgsProject.instance()

            # 移除所有图层
            layer_ids = list(project.mapLayers().keys())
            if layer_ids:
                project.removeMapLayers(layer_ids)
                logger.debug('%s 已移除 %d 个图层', log_prefix, len(layer_ids))

            # 清理项目
            project.clear()

            # 清理布局
            layout_manager = project.layoutManager()
            if layout_manager:
                for layout in list(layout_manager.layouts()):
                    layout_manager.removeLayout(layout)

            # 强制垃圾回收
            gc.collect()

            logger.debug('%s 会话资源已清理', log_prefix)

        except Exception as exc:
            logger.warning('%s 会话清理时发生异常: %s', log_prefix, exc)

    def register_layer(self, layer):
        """注册图层以便跟踪（使用弱引用）"""
        if layer:
            self._layers_registry.add(layer)

    def cleanup_all(self):
        """
        完全清理所有 QGIS 资源

        注意：通常不需要调用此方法，仅在应用关闭时使用
        """
        with self._resource_lock:
            try:
                _ensure_qgis_imports()
                project = QgsProject.instance()
                if project:
                    project.clear()

                # 清理弱引用中的图层
                self._layers_registry.clear()

                gc.collect()
                logger.info('QGIS 全部资源已清理')

            except Exception as exc:
                logger.error('QGIS 资源清理失败: %s', exc, exc_info=True)

    def ensure_initialized(self) -> bool:
        """
        确保 QGIS 已初始化的公共方法

        各地图生成模块可调用此方法替代本地的
        ``if not QgsApplication.instance(): ...`` 初始化逻辑，
        统一由 QGISManager 完成前缀路径设置和应用初始化。

        返回:
            bool: 初始化是否成功
        """
        return self._init_qgis()

    @property
    def is_initialized(self) -> bool:
        """检查 QGIS 是否已初始化"""
        return self._qgs_app is not None

    @property
    def active_sessions(self) -> int:
        """获取当前活动会话数"""
        return self._active_sessions


# 模块级别便捷函数
def get_qgis_manager() -> QGISManager:
    """获取 QGIS 管理器单例"""
    return QGISManager.get_instance()
