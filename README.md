# juchao — 巨潮资讯网公告爬取库

通过自建的 [PageSift](https://github.com/YOLOW92/pagesift) 服务（Firecrawl 兼容 API）抓取的**巨潮资讯网**（cninfo.com.cn）公告数据集。

## 数据来源

- 入口页面：<https://www.cninfo.com.cn/new/index.jsp>
- 公告查询 API：`POST https://www.cninfo.com.cn/new/hisAnnouncement/query`
- PDF 原文：`https://static.cninfo.com.cn/finalpage/...`

## 数据规模（首批）

| 项目 | 数量 |
|---|---|
| 公告元数据 | 600 条（`announcements.json`） |
| PDF 原文 | 600 个（`pdf/`，约 443 MB） |
| Markdown 正文 | 585 篇（`markdown/`，15 篇为扫描版 PDF 无可提取文本） |
| 公告日期窗口 | 2026-10-20 ~ 2026-10-21（该窗口共 2,382 条，本库收录前 600 条） |

## 目录结构

```
├── announcements.json   # 公告索引（代码/简称/标题/时间/PDF 链接）
├── markdown/            # 公告正文 Markdown（PageSift PDF→Markdown，每篇带元信息头）
├── pdf/                 # 公告 PDF 原文（文件名：日期_股票代码_公告ID.pdf）
└── scripts/             # 抓取与转换脚本
    ├── fetch_announcements.py    # 分页抓取公告元数据
    ├── download_pdfs.py          # 下载 PDF 原文
    └── convert_to_markdown.py    # 调用 PageSift 转 Markdown
```

## 抓取方式

1. `fetch_announcements.py` 按日期窗口（`seDate`）分页调用巨潮公告查询 API——直连 `pageNum` 分页在同时间戳公告较多时不稳定（实时插入导致 offset 漂移），固定历史日期窗口可稳定分页；规范化元数据并按 `announcementId` 去重；
2. `download_pdfs.py` 从 static.cninfo.com.cn 下载 PDF 原文（4 线程 + 断点续传）；
3. `convert_to_markdown.py` 调用本地部署的 PageSift（`http://localhost:3002/v1/scrape`）将 PDF 转换为 Markdown。

> 接口全量约 29 万条。修改脚本中的 `TOTAL_PAGES` / `SE_DATE` 可增量扩充；
> 抓取已做礼貌延迟（politeness）处理。

## 免责声明

数据版权归巨潮资讯网及原公告发布方所有，本库仅作技术演示与学习研究之用。
