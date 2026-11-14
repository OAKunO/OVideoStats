#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自媒体数据工具（脚本版）。

目前只做一件事，但做扎实：

    python3 -m smi bili https://space.bilibili.com/123456
        → 列出该主页全部视频的 播放量 和 点赞量

    python3 -m smi bili-video BV1xx411c7mD
        → 单条视频详情（这一条不需要登录态）
"""
