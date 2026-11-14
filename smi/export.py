#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出层：把采集结果写成 CSV 或 Excel。

Excel 走 openpyxl，做成真正能用的表格而不是纯文本转存：
  * 表头冻结 + 自动筛选
  * 播放量/点赞量用千分位数字格式（是数字，不是字符串，能直接排序求和）
  * 点赞率用百分比格式
  * 链接可点击
  * 第二个 sheet 放总览：中位数/均值，比平均数更抗爆款干扰
"""

import csv
import statistics
import time

from .bili import BiliError
from .fmt import fmt_ts, rate_value


def _cell(value):
    """Excel 只吃得下基本类型。字段偶尔是 dict/list（平台改结构时很容易发生），
    转成字符串而不是让整个导出崩掉。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict, list, tuple)):
        import json
        return json.dumps(value, ensure_ascii=False)[:200]
    return str(value)


def _link(v):
    """视频链接。B站按 bvid 拼，抖音等平台直接用数据里带的 url。"""
    return v.get("url") or "https://www.bilibili.com/video/%s" % v.get("bvid")


def write_csv(videos, path, id_header="bvid"):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["序号", id_header, "标题", "发布日期", "播放量", "点赞量",
                    "点赞率", "评论数", "时长", "链接"])
        for i, v in enumerate(videos, 1):
            rate = rate_value(v.get("like"), v.get("play"))
            w.writerow([
                i, v.get("bvid"), v.get("title"), fmt_ts(v.get("created"), default=""),
                v.get("play"), v.get("like"),
                "" if rate is None else "%.4f" % rate,
                v.get("comment") if v.get("comment") is not None else v.get("reply"),
                v.get("length"), _link(v),
            ])
    return path


def write_xlsx(videos, path, mid=None, total=None, id_header="bvid",
               account_label="账号 mid", extra_columns=None, stats_videos=None):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise BiliError(
            "导出 Excel 需要 openpyxl，请先安装：python3 -m pip install openpyxl\n"
            "或者改用 --out xxx.csv")

    wb = Workbook()

    # ---------------- Sheet 1：视频明细 ---------------- #
    ws = wb.active
    ws.title = "视频明细"

    headers = ["序号", "标题", "发布日期", "播放量", "点赞量", "点赞率"]
    extra_columns = extra_columns or []
    headers += [h for h, _ in extra_columns]
    headers += ["评论数", "时长", id_header, "链接"]
    extra_start = 7      # 序号/标题/发布/播放/点赞/点赞率 之后就是额外列
    ws.append(headers)

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4A6FA5")
    for col in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col)
        c.font = head_font
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center")

    for i, v in enumerate(videos, 1):
        comment = v.get("comment")
        if comment is None:
            comment = v.get("reply")
        row_values = [i, v.get("title"), fmt_ts(v.get("created"), default=""),
                      v.get("play"), v.get("like"),
                      rate_value(v.get("like"), v.get("play"))]
        row_values += [_cell(v.get(key)) for _, key in extra_columns]
        row_values += [comment, _cell(v.get("length")), v.get("bvid"), _link(v)]
        ws.append(row_values)
        row = ws.max_row
        ws.cell(row=row, column=4).number_format = "#,##0"
        ws.cell(row=row, column=5).number_format = "#,##0"
        ws.cell(row=row, column=6).number_format = "0.00%"
        last = len(headers)
        ws.cell(row=row, column=last - 2).number_format = "#,##0"
        link = ws.cell(row=row, column=last)
        link.hyperlink = link.value
        link.font = Font(color="0563C1", underline="single")

    widths = {"A": 6, "B": 52, "C": 12, "D": 12, "E": 12, "F": 10}
    for idx in range(len(extra_columns)):
        widths[get_column_letter(7 + idx)] = 14
    tail = 7 + len(extra_columns)
    widths[get_column_letter(tail)] = 10
    widths[get_column_letter(tail + 1)] = 9
    widths[get_column_letter(tail + 2)] = 15
    widths[get_column_letter(tail + 3)] = 34
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(headers)), ws.max_row)

    # ---------------- Sheet 2：总览 ---------------- #
    ov = wb.create_sheet("总览")
    # 统计口径可能与明细不同：抖音的「自见」作品播放量恒为 0，
    # 混进来会把中位数拉成 0，所以调用方可以单独指定参与统计的集合。
    stats = videos if stats_videos is None else stats_videos
    plays = [v["play"] for v in stats if v.get("play") is not None]
    likes = [v["like"] for v in stats if v.get("like") is not None]

    ov.append(["采集概览"])
    ov["A1"].font = Font(bold=True, size=14)
    ov.append([])
    rows = [
        (account_label, mid if mid else "-"),
        ("视频总数", total if total is not None else len(videos)),
        ("本次统计条数", len(videos)),
        ("参与统计的条数", len(stats)),
        ("其中非公开", len(videos) - len(stats)),
        ("采集时间", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("", ""),
        ("播放量 合计", sum(plays) if plays else None),
        ("播放量 中位数", int(statistics.median(plays)) if plays else None),
        ("播放量 均值", int(statistics.mean(plays)) if plays else None),
        ("播放量 最高", max(plays) if plays else None),
        ("播放量 最低", min(plays) if plays else None),
        ("", ""),
        ("点赞量 合计", sum(likes) if likes else None),
        ("点赞量 中位数", int(statistics.median(likes)) if likes else None),
        ("点赞量 均值", int(statistics.mean(likes)) if likes else None),
    ]
    for k, val in rows:
        ov.append([k, val])
    for r in range(3, ov.max_row + 1):
        ov.cell(row=r, column=1).font = Font(bold=True)
        ov.cell(row=r, column=2).number_format = "#,##0"

    start = ov.max_row + 2
    ov.cell(row=start, column=1, value="播放量 Top 10").font = Font(bold=True, size=12)
    ov.cell(row=start + 1, column=1, value="排名").font = Font(bold=True)
    ov.cell(row=start + 1, column=2, value="播放量").font = Font(bold=True)
    ov.cell(row=start + 1, column=3, value="点赞量").font = Font(bold=True)
    ov.cell(row=start + 1, column=4, value="标题").font = Font(bold=True)
    ranked = sorted([v for v in stats if v.get("play") is not None],
                    key=lambda v: v["play"], reverse=True)[:10]
    for i, v in enumerate(ranked, 1):
        r = start + 1 + i
        ov.cell(row=r, column=1, value=i)
        ov.cell(row=r, column=2, value=v.get("play")).number_format = "#,##0"
        ov.cell(row=r, column=3, value=v.get("like")).number_format = "#,##0"
        ov.cell(row=r, column=4, value=v.get("title"))

    ov.column_dimensions["A"].width = 18
    ov.column_dimensions["B"].width = 16
    ov.column_dimensions["C"].width = 14
    ov.column_dimensions["D"].width = 52

    wb.save(path)
    return path


def write_selected_xlsx(videos, path, selected, labels, account_name,
                        stats_videos=None, platform=None):
    """Write only requested columns from collector output; unavailable values stay blank."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from .fmt import fmt_ts

    if not selected:
        raise ValueError('至少选择一个字段')
    wb = Workbook()
    ws = wb.active
    ws.title = '作品明细'
    ws.append(['序号'] + [labels[key] for key in selected])
    for xlsx_cell in ws[1]:
        xlsx_cell.fill = PatternFill('solid', fgColor='315C88')
        xlsx_cell.font = Font(bold=True, color='FFFFFF')
        xlsx_cell.alignment = Alignment(vertical='center')
    ws.row_dimensions[1].height = 24

    for index, video in enumerate(videos, 1):
        values = [index]
        for key in selected:
            if key == 'like_rate':
                value = rate_value(video.get('like'), video.get('play'))
            elif key == 'created':
                value = fmt_ts(video.get('created'), default='')
            elif key == 'url':
                value = video.get('url') or ("https://www.bilibili.com/video/%s" % video.get('bvid') if platform == 'bili' and video.get('bvid') else None)
            else:
                value = video.get(key)
                if key == 'item_id' and value is None:
                    value = video.get('bvid')
            values.append(_cell(value))
        ws.append(values)
        for col, key in enumerate(selected, 2):
            xlsx_cell = ws.cell(index + 1, col)
            if key in ('like_rate','finish_rate','finish_rate_5s','cover_ctr','bounce_2s'):
                xlsx_cell.number_format = '0.00%'
            elif key in ('play','like','comment','share','collect','exposure','follow','recommend','fans_delta','profile_visits'):
                xlsx_cell.number_format = '#,##0'
            elif key == 'url' and xlsx_cell.value:
                xlsx_cell.hyperlink = xlsx_cell.value
                xlsx_cell.font = Font(color='0563C1', underline='single')
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(selected)+1)}{ws.max_row}'
    ws.column_dimensions['A'].width = 8
    for col, key in enumerate(selected, 2):
        ws.column_dimensions[get_column_letter(col)].width = 54 if key in ('title','url') else 18

    stats = videos if stats_videos is None else stats_videos
    summary = wb.create_sheet('总览')
    summary.append(['账号', account_name])
    summary.append(['作品数', len(videos)])
    summary.append(['参与统计', len(stats)])
    summary.append(['采集时间', time.strftime('%Y-%m-%d %H:%M:%S')])
    for key in ('play','like'):
        numbers = [v.get(key) for v in stats if isinstance(v.get(key), (int, float))]
        label = labels.get(key, key)
        summary.append([f'{label}合计', sum(numbers) if numbers else None])
        summary.append([f'{label}中位数', statistics.median(numbers) if numbers else None])
    summary.column_dimensions['A'].width = 20
    summary.column_dimensions['B'].width = 32
    wb.save(path)
    return path
