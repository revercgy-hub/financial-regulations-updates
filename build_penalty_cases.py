import argparse
import hashlib
import html
import io
import json
import re
import shutil
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path("penalty_cases_kb")
SITE = Path("penalty_cases_site")
MOF_LIST_URL = "https://www.mof.gov.cn/gp/xxgkml/index_8254.htm"
MOF_BASE_URL = "https://www.mof.gov.cn/gp/xxgkml/"
MOF_HIDDEN_DEBT_NOTICES = (
    "https://jdjc.mof.gov.cn/jianchagonggao/202311/t20231106_3914898.htm",
    "https://jdjc.mof.gov.cn/jianchagonggao/202504/t20250418_3962254.htm",
    "https://jdjc.mof.gov.cn/jianchagonggao/202508/t20250801_3969211.htm",
)
CSRC_CHANNEL_ID = "28de6b87eda140cb93de4dd10d11867d"
CSRC_LIST_URL = (
    "https://www.csrc.gov.cn/searchList/"
    f"{CSRC_CHANNEL_ID}?_isAgg=true&_isJson=true&_pageSize={{page_size}}"
    "&_template=index&_rangeTimeGte=&_channelName=&page={page}"
)
AUDIT_LIST_URL = "https://www.audit.gov.cn/n5/n25/index.html"
WAYBACK_PREFIX = "https://web.archive.org/web/20240601000000id_/"
CCDI_SOURCE = "中央纪委国家监委"
CCDI_CACHE_PATH = Path("ccdi_sources") / "list_cache.json"
CCDI_CHANNELS = [
    {
        "url": "https://www.ccdi.gov.cn/scdcn/zggb/zjsc/",
        "cadre_level": "中管干部",
        "case_stage": "执纪审查",
    },
    {
        "url": "https://www.ccdi.gov.cn/scdcn/zggb/djcf/",
        "cadre_level": "中管干部",
        "case_stage": "党纪政务处分",
    },
    {
        "url": "https://www.ccdi.gov.cn/scdcn/zyyj/zjsc/",
        "cadre_level": "中央一级党和国家机关、国企和金融单位干部",
        "case_stage": "执纪审查",
    },
    {
        "url": "https://www.ccdi.gov.cn/scdcn/zyyj/djcf/",
        "cadre_level": "中央一级党和国家机关、国企和金融单位干部",
        "case_stage": "党纪政务处分",
    },
]
AUDIT_NOTICE_URLS = [
    {"url": "https://www.audit.gov.cn/n5/n25/c63525/content.html", "file_no": "2010年第20号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c63530/content.html", "file_no": "2011年第2号公告", "expected": 28},
    {"url": "https://www.audit.gov.cn/n5/n25/c63558/content.html", "file_no": "2011年第29号公告", "expected": 39},
    {"url": "https://www.audit.gov.cn/n5/n25/c63571/content.html", "file_no": "2012年第2号公告", "expected": 30},
    {"url": "https://www.audit.gov.cn/n5/n25/c63608/content.html", "file_no": "2012年第35号公告", "expected": 38},
    {"url": "https://www.audit.gov.cn/n5/n25/c63634/content.html", "file_no": "2013年第26号公告", "expected": 38},
    {"url": "https://www.audit.gov.cn/n5/n25/c63640/content.html", "file_no": "2013年第30号公告", "expected": 15},
    {"url": "https://www.audit.gov.cn/n5/n25/c63644/content.html", "file_no": "2014年第2号公告", "expected": 19},
    {"url": "https://www.audit.gov.cn/n5/n25/c63650/content.html", "file_no": "2014年第8号公告", "expected": 35},
    {"url": "https://www.audit.gov.cn/n5/n25/c61437/content.html", "file_no": "2014年第23号公告", "expected": 40},
    {"url": "https://www.audit.gov.cn/n5/n25/c67425/content.html", "file_no": "2015年第22号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c84810/content.html", "file_no": "2016年第27号公告", "expected": 39},
    {"url": "https://www.audit.gov.cn/n5/n25/c91862/content.html", "file_no": "2016年第31号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c97012/content.html", "file_no": "2017年第30号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c118707/content.html", "file_no": "2018年第1号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c123558/content.html", "file_no": "2018年第42号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c133004/content.html", "file_no": "2019年第5号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c139902/content.html", "file_no": "2020年第3号公告"},
    {"url": "https://www.audit.gov.cn/n5/n25/c10179954/content.html", "file_no": "2021年第5号公告", "expected": 24},
    {"url": "https://www.audit.gov.cn/n5/n25/c10307951/content.html", "file_no": "2022年第2号公告", "expected": 26},
]
AUDIT_TEXT_FALLBACKS = {
    "https://www.audit.gov.cn/n5/n25/c139902/content.html": Path("audit_sources") / "审计署_2020年第3号公告.txt",
    "https://www.audit.gov.cn/n5/n25/c10307951/content.html": Path("audit_sources") / "审计署_2022年第2号公告.txt",
}
AUDIT_TITLE_OVERRIDES = {
    "2020年第3号公告": [
        "原中国船舶重工集团有限公司总经理孙波涉嫌利用职务之便为他人谋取利益问题",
        "中信银行原党委副书记、行长孙德顺涉嫌接受利益输送问题",
        "中煤科技集团有限公司原总经理陈伟涉嫌违规协助外部企业办理保理业务问题",
        "上海联东地中海国际船舶代理有限公司原总经理邹斌涉嫌受贿问题",
        "中国银行湖南省分行原国际业务部总经理周云伯涉嫌受贿问题",
        "徽商银行公司银行部原总经理吴耘涉嫌违规入股金融公司问题",
        "山西省平遥县财政局原统评股股长常斗录等人涉嫌玩忽职守问题",
        "重庆市酉阳县住房城乡建设委员会房管科原科长彭华涉嫌违规挪用住宅专项维修资金问题",
        "山西省吕梁市石楼县龙交乡阳崖村原党支部书记贺淑林涉嫌骗取财政资金问题",
        "黑龙江省桦南县和湖南省新晃县4名公职人员涉嫌在工程建设中违规问题",
        "黑龙江省兰西县、望奎县、海伦市部分村镇工作人员涉嫌骗取财政补贴问题",
        "云南省香格里拉市水务局原副局长马卫民等人未认真履职导致财政资金被骗取套取问题",
        "鸡西市工业和信息化委员会原工作人员于某涉嫌骗取职工养老金问题",
        "黑龙江省医院康复科护士王某涉嫌骗取医保基金问题",
    ],
    "2022年第2号公告": [
        "中国石油天然气集团有限公司所属企业涉嫌倒卖进口原油问题",
        "一些债务沉重地区违规兴建楼堂馆所问题",
        "中国东方资产管理股份有限公司原党委委员、副总裁胡小钢涉嫌违规决策造成国有资产损失问题",
        "国家税务总局山东省税务局总会计师高萍涉嫌受贿问题",
        "中国农业发展银行原巡视工作办公室主任贾楞涉嫌违规放贷问题",
        "云南省曲靖市人大常委会原副主任傅学宾涉嫌受贿问题",
        "北京市农业农村局涉嫌利用企业出具的虚假材料完成高标准农田建设任务问题",
        "中国进出口银行上海分行原党委书记、行长李莅涉嫌违规干预贷款发放问题",
        "中国人民财产保险股份有限公司青岛分公司涉嫌骗取保险理赔金和财政补贴问题",
        "淄博齐翔石油化工集团有限公司涉嫌内幕交易问题",
        "建设银行吉林分行原党委书记、行长张勤涉嫌为特定企业融资提供便利问题",
        "辽宁省沈阳市自然资源保护行政执法支队辽中大队临时负责人韩斌斌涉嫌失职渎职放任基本农田被长期占用问题",
        "中国银行河南分行原副行长周路涉嫌违规决策问题",
        "山西华泰会计师事务所有限公司和山西博林会计师事务所有限公司涉嫌出具虚假审计报告帮助部分企业骗取财政奖励资金问题",
        "浦发银行贵阳分行原行长王兴涉嫌违规放贷问题",
        "富滇银行原副行长曹艳丽涉嫌违规放贷问题",
        "阳光农业相互保险公司所属11个保险社、中国人寿财产保险股份有限公司所属1家公司、中国人民财产保险股份有限公司所属2家公司涉嫌审核把关不严，造成农业保险财政补贴和赔偿金被骗取等问题",
        "福建省平和县、建宁县、永春县和仙游县相关单位涉嫌非法占用永久基本农田、高标准农田等问题",
        "甘肃省华池县发展改革局原局长李雨、副局长方建鹏等人涉嫌履职不力造成国有资产损失问题",
        "四川省昭觉县农业农村局原局长罗俊、机械化股原股长万勇等人涉嫌高价采购造成财政资金损失问题",
        "广西壮族自治区昭平县交通运输局副局长卢威涉嫌失职导致扶贫资金被骗取问题",
        "安徽省砀山县李庄镇原人大主席倪铭键涉嫌套取扶贫资金问题",
        "黑龙江省依安县和克山县3名乡镇干部涉嫌失职渎职导致农业补贴资金被骗取问题",
        "陕西省周至县板房子镇原镇长杨远涉嫌虚报农村户厕改造项目完成数套取财政资金问题",
        "广东省怀集县龙村原村党支部书记、村委会主任卢星帮等人涉嫌侵占村集体资产问题",
        "吉林省长白朝鲜族自治县铭远新能源科技有限责任公司原董事长金哲涉嫌违规审批出借公司资金问题",
    ],
}
AUDIT_SUMMARY_OVERRIDES = {
    "2020年第3号公告": [
        "孙波被开除党籍、开除公职；以受贿罪、国有公司人员滥用职权罪被判处有期徒刑12年，并处罚金80万元，追缴全部违法所得。",
        "孙德顺被开除党籍，取消相关待遇，收缴违纪违法所得，并移送检察机关审查起诉。",
        "陈伟以单位受贿罪、受贿罪被判处有期徒刑11年，并处罚金200万元，追缴违法所得1400万元。",
        "邹斌以受贿罪被判处有期徒刑12年，并处罚金200万元，追缴赃款予以没收。",
        "周云伯以违规出具金融票证罪、受贿罪被判处有期徒刑6年，并处罚金20万元，没收违法所得；黄勇被判处有期徒刑5年，并处罚金300万元。",
        "吴耘被开除党籍、取消退休待遇，追缴其及其他涉案人员非法所得8445万元，并移送检察机关审查起诉。",
        "常斗录等3人被开除党籍、开除公职，6人受到党内警告、党内严重警告等处分，3人受到政务警告处分。",
        "彭华被开除党籍、开除公职，并移送司法机关。",
        "贺淑林受到开除党籍处分，被判处有期徒刑1年4个月、缓刑2年，并处罚金5000元。",
        "4名公职人员分别受到留党察看、党内严重警告、免职和政务降级等处理处分，并处罚金。",
        "16人受到党内警告、党内严重警告、开除党籍等处分，其中2人分别被判处2年或5年有期徒刑。",
        "马卫民被开除党籍、开除公职，以受贿罪被判处有期徒刑1年、缓刑2年，并处罚金12万元；其他责任人受到相应处理处分。",
        "于某以诈骗罪被判处有期徒刑6个月，并处罚金4万元，追缴资金7.32万元。",
        "王某以诈骗罪被判处有期徒刑3年、缓刑4年，并处罚金3万元，追缴资金22.35万元。",
    ],
    "2022年第2号公告": [
        "有关部门对倒卖进口原油问题依规依纪依法严肃处理，对违法违规获利予以追缴。",
        "国务院办公厅对部分债务沉重地区违规兴建楼堂馆所问题公开通报并要求监督问责。",
        "胡小钢被开除党籍、开除公职，收缴违纪违法所得，并移送司法机关。",
        "高萍被开除党籍、开除公职，收缴违纪违法所得，并移送司法机关。",
        "贾楞被开除党籍，并移送司法机关。",
        "傅学宾被开除党籍、开除公职，并移送司法机关。",
        "北京市农业农村局2名责任人被免职或批评教育；相关企业及3名责任人被处以罚款。",
        "李莅被开除党籍、开除公职，收缴违纪违法所得，并移送司法机关。",
        "青岛分公司及所属公司4名责任人被警告，并处罚款合计123万元。",
        "该公司被警告，没收公司及相关人员违法所得，并处罚款2194.39万元。",
        "张勤被开除党籍、取消退休待遇，收缴违纪违法所得，并移送司法机关。",
        "韩斌斌受到党内警告处分。",
        "周路被开除党籍、开除公职，收缴违纪违法所得，并移送司法机关。",
        "两家会计师事务所被没收违法所得和罚款合计74.6万元，山西华泰会计师事务所有限公司被暂停经营业务6个月。",
        "王兴被开除党籍，收缴违纪违法所得，并移送司法机关。",
        "曹艳丽被开除党籍、开除公职，收缴违纪违法所得，并移送司法机关。",
        "相关单位被处罚款合计505万元；18名责任人被警告，并处罚款合计95万元。",
        "相关单位完成整改、退回财政补助资金，并被处罚款。",
        "李雨、方建鹏等5人分别受到党内严重警告、党内警告和降级等处分。",
        "罗俊被开除党籍、政务撤职；万勇被开除党籍、开除公职，并移送司法机关。",
        "卢威受到党内警告处分。",
        "倪铭键等4人受到党内严重警告、党内警告、记大过等处分，并收缴违纪违法所得。",
        "3名乡镇干部分别受到党内警告、政务警告、诫勉等处理处分。",
        "杨远受到党内严重警告、政务记大过处分。",
        "卢星帮等5人受到党内严重警告和党内警告等处分。",
        "金哲被开除党籍、开除公职。",
    ],
}
USER_AGENT = "LocalPenaltyCasesBuilder/1.0 (+personal offline archive)"


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ").replace("\u2002", " ").replace("\u3000", " ")
    value = value.replace("&ensp;", " ").replace("&nbsp;", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_filename(value: str, max_len: int = 100) -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:max_len].rstrip(" .") or "untitled"
    # Android/ext4 limits one path component to 255 bytes. Keep room for the
    # extension and for future suffixes while preserving the unique case ID at
    # the beginning of every generated document name.
    while len(value.encode("utf-8")) > 200:
        value = value[:-1].rstrip(" .")
    return value or "untitled"


def request_text(session: requests.Session, url: str, timeout: int = 30) -> str:
    last_error = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def request_json(session: requests.Session, url: str, timeout: int = 30) -> dict:
    last_error = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def request_audit_resource(session: requests.Session, url: str, timeout: int = 60) -> tuple[bytes, str]:
    last_error = None
    candidates = [url, WAYBACK_PREFIX + url]
    for index, candidate in enumerate(candidates):
        attempts = 1 if index == 0 else 3
        for attempt in range(1, attempts + 1):
            try:
                response = session.get(candidate, timeout=timeout)
                response.raise_for_status()
                return response.content, candidate
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch current or archived copy of {url}: {last_error}") from last_error


def decode_html(content: bytes) -> str:
    response = requests.Response()
    response._content = content
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def text_from_html_fragment(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    parts = []
    blocks = soup.find_all(["h1", "h2", "h3", "p", "li"])
    if not blocks:
        blocks = [
            tag
            for tag in soup.find_all(["div", "td"])
            if not tag.find(["div", "td", "p", "li", "h1", "h2", "h3"])
        ]
    for block in blocks:
        text = clean_text(block.get_text(" ", strip=True))
        if text:
            parts.append(text)
    if not parts:
        parts = [clean_text(soup.get_text("\n", strip=True))]
    deduped = []
    seen = set()
    for part in parts:
        if part not in seen:
            deduped.append(part)
            seen.add(part)
    return "\n\n".join(deduped).strip()


def extract_meta_pairs_from_mof(soup: BeautifulSoup) -> dict:
    labels = {
        "名称",
        "索引号",
        "文号",
        "发文时间",
        "发文机构",
        "主题",
        "体裁",
        "备注",
    }
    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in soup.find_all(["td", "th"])]
    pairs = {}
    for index, cell in enumerate(cells[:-1]):
        if cell in labels and cells[index + 1]:
            pairs[cell] = cells[index + 1]
    return pairs


def parse_mof_list(session: requests.Session) -> list[dict]:
    source = request_text(session, MOF_LIST_URL, timeout=60)
    soup = BeautifulSoup(source, "lxml")
    records = []
    for row in soup.select("tbody#sub tr"):
        link = row.find("a", href=True)
        if not link:
            continue
        script = link.find("script")
        title = ""
        if script and script.string:
            match = re.search(r'var\s+str\s*=\s*"([^"]+)"', script.string)
            if match:
                title = match.group(1)
        title = clean_text(title or link.get_text(" ", strip=True))
        if "行政处罚决定书" not in title:
            continue
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        file_no = cells[1] if len(cells) > 1 else ""
        publish_date = cells[2] if len(cells) > 2 else ""
        url = urljoin(MOF_LIST_URL, link["href"])
        records.append(
            {
                "source": "财政部",
                "title": title,
                "file_no": file_no,
                "publish_date": publish_date,
                "url": url,
            }
        )
    return records


def parse_mof_case(session: requests.Session, record: dict) -> dict:
    source = request_text(session, record["url"], timeout=45)
    soup = BeautifulSoup(source, "lxml")
    meta = extract_meta_pairs_from_mof(soup)
    content = (
        soup.select_one(".sqxzbList2")
        or soup.find(class_=re.compile(r"TRS_Editor|article|content", re.I))
        or soup.find(id=re.compile(r"zoom|content", re.I))
        or soup.find("body")
    )
    body_text = text_from_html_fragment(str(content)) if content else clean_text(soup.get_text("\n", strip=True))
    title = meta.get("名称") or record["title"]
    file_no = meta.get("文号") or record.get("file_no", "")
    publish_date = meta.get("发文时间") or record.get("publish_date", "")
    return {
        **record,
        "title": clean_text(title),
        "file_no": clean_text(file_no),
        "publish_date": clean_text(publish_date),
        "agency": "财政部",
        "department": meta.get("发文机构", "监督评价局"),
        "category": meta.get("主题", "行政处罚结果"),
        "body": body_text,
        "raw_html": source,
    }


def request_mof_notice(session: requests.Session, url: str) -> str:
    last_error = None
    for attempt in range(1, 9):
        target = f"{url}?finreg_retry={time.time_ns()}_{attempt}"
        try:
            response = session.get(target, timeout=45)
            response.raise_for_status()
            if len(response.content) < 5000:
                raise RuntimeError("unexpectedly short MOF notice response")
            response.encoding = response.apparent_encoding or "utf-8"
            if "502 Bad Gateway" in response.text:
                raise RuntimeError("MOF gateway returned an error page")
            return response.text
        except Exception as error:
            last_error = error
            if attempt < 8:
                time.sleep(min(attempt, 3))
    raise RuntimeError(f"failed to fetch hidden-debt notice {url}: {last_error}") from last_error


def parse_mof_hidden_debt_cases(session: requests.Session) -> list[dict]:
    """Split each official hidden-debt roundup into searchable individual cases."""
    records = []
    ordinal_pattern = re.compile(r"^([一二三四五六七八九十]+)、(.+)")
    for notice_url in MOF_HIDDEN_DEBT_NOTICES:
        source = request_mof_notice(session, notice_url)
        soup = BeautifulSoup(source, "lxml")
        content = (
            soup.select_one(".TRS_Editor")
            or soup.select_one(".article_con")
            or soup.select_one("#zoom")
            or soup.find(class_=re.compile(r"article.*content|content.*article", re.I))
        )
        if content is None:
            raise RuntimeError(f"hidden-debt notice body not found: {notice_url}")
        title_node = soup.find(["h1", "h2"])
        notice_title = clean_text(title_node.get_text(" ", strip=True)) if title_node else "财政部地方政府隐性债务问责典型案例通报"
        page_text = clean_text(soup.get_text("\n", strip=True))
        date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s+来源[：:]?\s*监督评价局", page_text)
        if date_match:
            publish_date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        else:
            url_date = re.search(r"/t(20\d{2})(\d{2})(\d{2})_", notice_url)
            publish_date = "-".join(url_date.groups()) if url_date else ""
        found = 0
        for block in content.find_all("p"):
            text = clean_text(block.get_text(" ", strip=True))
            match = ordinal_pattern.match(text)
            if not match or len(text) < 80:
                continue
            ordinal, remainder = match.groups()
            headline = remainder.split("。", 1)[0].strip(" 。")
            found += 1
            records.append(
                {
                    "source": "财政部",
                    "agency": "财政部",
                    "department": "监督评价局",
                    "category": "隐性债务问责通报",
                    "case_topic": "隐性债务",
                    "title": f"隐性债务典型案例：{headline}",
                    "file_no": "",
                    "publish_date": publish_date,
                    "url": f"{notice_url}#hidden-debt-{ordinal}",
                    "body": text,
                    "raw_html": source,
                    "notice_title": notice_title,
                }
            )
        if found == 0:
            raise RuntimeError(f"no individual hidden-debt cases parsed: {notice_url}")
        print(f"MOF hidden debt: parsed {found} cases from {publish_date}")
    return records


def meta_value(item: dict, key: str) -> str:
    for group in item.get("domainMetaList", []):
        for meta in group.get("resultList", []):
            if meta.get("name") == key or meta.get("key") == key:
                return clean_text(str(meta.get("value") or ""))
    return ""


def parse_csrc_list(session: requests.Session, limit: int | None = None, page_size: int = 50) -> list[dict]:
    first = request_json(session, CSRC_LIST_URL.format(page_size=page_size, page=1), timeout=60)
    total = int(first.get("data", {}).get("total") or 0)
    actual_page_size = int(first.get("data", {}).get("rows") or len(first.get("data", {}).get("results", [])) or page_size)
    if limit:
        total = min(total, limit)
    pages = (total + actual_page_size - 1) // actual_page_size
    items = []
    for page in range(1, pages + 1):
        data = first if page == 1 else request_json(
            session, CSRC_LIST_URL.format(page_size=page_size, page=page), timeout=60
        )
        for item in data.get("data", {}).get("results", []):
            if limit and len(items) >= limit:
                break
            url = item.get("url") or ""
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = urljoin("https://www.csrc.gov.cn", url)
            content_html = item.get("contentHtml") or ""
            body = text_from_html_fragment(content_html) or clean_text(item.get("content", ""))
            file_no = meta_value(item, "文号") or extract_file_no(body)
            publish_date = meta_value(item, "发文日期") or item.get("publishedTimeStr", "")
            items.append(
                {
                    "source": "证监会",
                    "agency": "中国证监会",
                    "department": meta_value(item, "部门") or meta_value(item, "发布机构"),
                    "category": item.get("channelName") or "行政处罚",
                    "title": clean_text(item.get("title") or item.get("subTitle") or "中国证券监督管理委员会行政处罚决定书"),
                    "file_no": clean_text(file_no),
                    "publish_date": clean_text(str(publish_date)[:10]),
                    "url": url,
                    "body": body,
                    "raw_html": content_html,
                    "manuscript_id": str(item.get("manuscriptId") or ""),
                }
            )
        if limit and len(items) >= limit:
            break
        time.sleep(0.15)
    return items


def request_ccdi_text(url: str, timeout: int = 45) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    source = response.text
    if "captchaPage" in source or "seccaptcha" in source or len(source) < 2500:
        raise RuntimeError("官网触发访问校验，请稍后重试；已保留本地缓存")
    return source


def ccdi_page_url(base_url: str, page: int) -> str:
    return base_url if page == 0 else urljoin(base_url, f"index_{page}.html")


def parse_ccdi_list_page(source: str, page_url: str, channel: dict) -> tuple[list[dict], int]:
    soup = BeautifulSoup(source, "lxml")
    page_match = re.search(r"createPageHTML\(\s*(\d+)\s*,", source)
    total_pages = int(page_match.group(1)) if page_match else 1
    records = []
    for item in soup.select("li"):
        link = item.find("a", href=True)
        if not link:
            continue
        item_text = clean_text(item.get_text(" ", strip=True))
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", item_text)
        title = clean_text(link.get_text(" ", strip=True)).lstrip("\ufeff\u200b")
        if channel["case_stage"] == "执纪审查" and re.search(r"(?:\.\.\.|…)$", title):
            marker = title.find("接受中央纪委国家监委")
            if marker != -1:
                title = title[:marker] + "接受中央纪委国家监委纪律审查和监察调查"
        if not date_match or not title:
            continue
        if channel["case_stage"] == "执纪审查":
            if not re.search(r"审查调查|监察调查|纪律审查", title):
                continue
        elif not re.search(r"处分|双开|开除党籍|开除公职", title):
            continue
        records.append(
            {
                "source": CCDI_SOURCE,
                "agency": CCDI_SOURCE,
                "department": "中央纪委国家监委网站",
                "category": channel["case_stage"],
                "case_stage": channel["case_stage"],
                "cadre_level": channel["cadre_level"],
                "title": title,
                "file_no": "",
                "publish_date": date_match.group(0),
                "url": urljoin(page_url, link["href"]),
                "body": title,
                "raw_html": "",
            }
        )
    if not records:
        raise RuntimeError(f"未在栏目页识别到案件通告：{page_url}")
    return records, total_pages


def load_ccdi_cache() -> list[dict]:
    if not CCDI_CACHE_PATH.is_file():
        return []
    try:
        data = json.loads(CCDI_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_ccdi_cache(records: list[dict]) -> None:
    CCDI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cached = [
        {key: value for key, value in record.items() if key not in {"body", "raw_html", "case_id"}}
        for record in records
    ]
    CCDI_CACHE_PATH.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")


def ccdi_article_text(source: str) -> str:
    soup = BeautifulSoup(source, "lxml")
    content = (
        soup.select_one(".content")
        or soup.select_one(".TRS_Editor")
        or soup.select_one("#UCAP-CONTENT")
        or soup.select_one(".article-content")
    )
    if not content:
        return ""
    for tag in content(["script", "style", "noscript"]):
        tag.decompose()
    for br in content.find_all("br"):
        br.replace_with("\n")
    return clean_text(content.get_text("\n", strip=True))


def parse_ccdi_cases(
    limit: int | None = None,
    pages_per_channel: int = 1,
    detail_limit: int = 0,
) -> list[dict]:
    cached_records = load_ccdi_cache()
    by_url = {record.get("url", ""): record for record in cached_records if record.get("url")}
    fetched_pages = 0
    for channel in CCDI_CHANNELS:
        total_pages = pages_per_channel
        for page in range(max(1, pages_per_channel)):
            if page >= total_pages:
                break
            page_url = ccdi_page_url(channel["url"], page)
            try:
                source = request_ccdi_text(page_url)
                page_records, discovered_pages = parse_ccdi_list_page(source, page_url, channel)
                total_pages = min(discovered_pages, max(1, pages_per_channel))
                for record in page_records:
                    by_url[record["url"]] = record
                fetched_pages += 1
            except Exception as exc:
                print(f"CCDI list fetch failed: {page_url} ({exc})")
                break
            time.sleep(1.0)

    all_records = sorted(
        by_url.values(),
        key=lambda item: (item.get("publish_date", ""), item.get("case_stage", ""), item.get("title", "")),
        reverse=True,
    )
    if fetched_pages:
        write_ccdi_cache(all_records)
    if not all_records:
        raise RuntimeError("未能读取中央纪委国家监委栏目，且没有可用的本地栏目缓存")
    records = all_records[:limit] if limit else all_records

    for index, record in enumerate(records, start=1):
        record = dict(record)
        record.setdefault("body", record["title"])
        record.setdefault("raw_html", "")
        if index <= detail_limit:
            try:
                source = request_ccdi_text(record["url"])
                body = ccdi_article_text(source)
                if body:
                    record["body"] = body
                    record["raw_html"] = source
            except Exception as exc:
                print(f"CCDI detail fetch failed: {record['url']} ({exc})")
            time.sleep(1.0)
        record["case_id"] = f"中央纪委国家监委-{index:05d}"
        records[index - 1] = record
    return records


def normalize_publish_date(value: str) -> str:
    match = re.search(r"(20\d{2}|19\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", value or "")
    if not match:
        return clean_text(value)[:10]
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def clean_audit_text(value: str) -> str:
    noise_patterns = [
        r"^您的位置[:：]",
        r"^当前位置[:：]",
        r"^首页\s*[>＞]",
        r"^字号[:：]",
        r"^打印本页$",
        r"^关闭窗口$",
    ]
    parts = []
    for part in clean_text(value).split("\n\n"):
        part = clean_text(part)
        if part and not any(re.search(pattern, part) for pattern in noise_patterns):
            parts.append(part)
    return "\n\n".join(parts)


def compact_chinese_spacing(value: str) -> str:
    value = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff，。；：、])", "", value)
    value = re.sub(r"(?<=[，。；：、])[ \t]+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=\d)", "", value)
    value = re.sub(r"(?<=\d)[ \t]+(?=[\u4e00-\u9fff])", "", value)
    return value


def original_audit_asset_url(notice_url: str, href: str) -> str:
    archived = re.search(r"(https?://www\.audit\.gov\.cn/[^?#]+)", href or "")
    if archived:
        return archived.group(1)
    return urljoin(notice_url, href)


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return clean_audit_text("\n\n".join(page.extract_text() or "" for page in reader.pages))


def audit_text_from_node(node) -> str:
    fragment = BeautifulSoup(str(node), "lxml")
    for tag in fragment(["script", "style", "noscript"]):
        tag.decompose()
    for br in fragment.find_all("br"):
        br.replace_with("\n")
    return clean_audit_text(fragment.get_text("\n", strip=True))


def audit_article_text(soup: BeautifulSoup) -> str:
    candidates = []
    for selector in (
        "#textSize",
        "#UCAP-CONTENT",
        ".TRS_Editor",
        ".news-text-all",
        ".con-article-content",
        ".article-content",
        ".content-all",
    ):
        for node in soup.select(selector):
            value = audit_text_from_node(node)
            if value:
                candidates.append(value)
    return max(candidates, key=len) if candidates else ""


def audit_notice_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in (".con-article-title", ".title-all", ".article-title", "h1"):
        node = soup.select_one(selector)
        value = clean_text(node.get_text(" ", strip=True)) if node else ""
        if value and ("公告" in value or "审计署移送" in value):
            return value
    if soup.title:
        value = clean_text(re.split(r"[_|]审计署", soup.title.get_text(" ", strip=True))[0])
        if value:
            return value
    return fallback


def split_audit_cases(text: str) -> list[tuple[str, str]]:
    normalized = clean_audit_text(text)
    normalized = re.sub(
        r"\s*(?<!第)([一二三四五六七八九十百]{1,3})、(?=[\s\S]{2,150}?(?:问题|案件|事项))",
        r"\n\1、",
        normalized,
    )
    marker_pattern = re.compile(r"(?m)^\s*(?P<number>[一二三四五六七八九十百]{1,3}|\d{1,3})[、．.]\s*")
    markers = list(marker_pattern.finditer(normalized))
    cases = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(normalized)
        segment = clean_audit_text(normalized[marker.end() : end])
        if len(segment) < 50:
            continue
        title_match = re.match(
            r"([\s\S]{2,180}?(?:问题|案件|事项))(?=\s*(?:审计发现|经审计|审计署|调查发现|[12]\d{3}年))",
            segment,
        )
        if title_match:
            title = re.sub(r"\s+", "", title_match.group(1))
            body = clean_audit_text(segment[title_match.end() :])
        else:
            first_line = clean_text(segment.split("\n", 1)[0])
            title = first_line[:120]
            body = segment
        if title and body:
            cases.append((title, body))
    return cases


def parse_audit_notice(session: requests.Session, notice: dict) -> list[dict]:
    content, fetched_url = request_audit_resource(session, notice["url"])
    source = decode_html(content)
    soup = BeautifulSoup(source, "lxml")
    fallback_title = f"{notice['file_no']}：审计署移送违纪违法问题线索查处情况"
    notice_title = audit_notice_title(soup, fallback_title)
    page_text = clean_text(soup.get_text("\n", strip=True))
    publish_date = normalize_publish_date(page_text)
    body = audit_article_text(soup)

    if len(split_audit_cases(body)) < 2:
        for link in soup.select('a[href*=".pdf"], a[href*=".PDF"]'):
            pdf_url = original_audit_asset_url(notice["url"], link.get("href", ""))
            try:
                pdf_content, _ = request_audit_resource(session, pdf_url)
                pdf_text = extract_pdf_text(pdf_content)
                if len(pdf_text) > len(body):
                    body = pdf_text
            except Exception as exc:
                print(f"AUDIT PDF fetch failed: {pdf_url} ({exc})")

    fallback_path = AUDIT_TEXT_FALLBACKS.get(notice["url"])
    if len(split_audit_cases(body)) < 2 and fallback_path and fallback_path.exists():
        body = clean_audit_text(compact_chinese_spacing(fallback_path.read_text(encoding="utf-8")))

    cases = split_audit_cases(body)
    expected = notice.get("expected")
    if expected and len(cases) != expected:
        print(f"AUDIT split warning: {notice['file_no']} expected {expected}, found {len(cases)}")
    if not cases:
        raise RuntimeError(f"no numbered cases found in {fetched_url}")

    records = []
    title_overrides = AUDIT_TITLE_OVERRIDES.get(notice["file_no"], [])
    for case_index, (title, case_body) in enumerate(cases, start=1):
        if case_index <= len(title_overrides):
            title = title_overrides[case_index - 1]
        records.append(
            {
                "source": "审计署",
                "agency": "审计署",
                "department": "办公厅",
                "category": "移送违纪违法问题线索查处通报",
                "title": title,
                "notice_title": notice_title,
                "file_no": notice["file_no"],
                "publish_date": publish_date,
                "url": f"{notice['url']}#case-{case_index:02d}",
                "notice_url": notice["url"],
                "body": case_body,
                "raw_html": source,
            }
        )
    return records


def parse_audit_cases(session: requests.Session, limit: int | None = None) -> list[dict]:
    records = []
    for notice in AUDIT_NOTICE_URLS:
        try:
            notice_cases = parse_audit_notice(session, notice)
            records.extend(notice_cases)
            print(f"  AUDIT {notice['file_no']}: {len(notice_cases)} cases")
        except Exception as exc:
            print(f"AUDIT fetch failed: {notice['url']} ({exc})")
        if limit and len(records) >= limit:
            records = records[:limit]
            break
        time.sleep(0.15)
    for index, record in enumerate(records, start=1):
        record["case_id"] = f"审计署-{index:05d}"
    return records


def extract_file_no(text: str) -> str:
    match = re.search(r"(财监法〔\d{4}〕\s*\d+\s*号|〔\d{4}〕\s*\d+\s*号)", text)
    return clean_text(match.group(1)) if match else ""


def detect_violation_type(text: str, source: str = "") -> str:
    if re.search(r"隐性债务|违法违规举债|新增政府债务|化债不实|少报漏报债务", text):
        return "地方政府隐性债务"
    if source == CCDI_SOURCE:
        ccdi_patterns = [
            ("违反政治纪律", "违反政治纪律"),
            ("违反中央八项规定精神", "违反中央八项规定精神"),
            ("违反组织纪律", "违反组织纪律"),
            ("违反廉洁纪律", "违反廉洁纪律"),
            ("违反群众纪律", "违反群众纪律"),
            ("违反工作纪律", "违反工作纪律"),
            ("违反生活纪律", "违反生活纪律"),
            ("利用影响力受贿", "受贿及利用影响力受贿"),
            ("涉嫌受贿", "涉嫌受贿犯罪"),
            ("滥用职权", "滥用职权及失职渎职"),
            ("严重违纪违法", "严重违纪违法"),
            ("双开", "严重违纪违法"),
            ("开除党籍", "严重违纪违法"),
            ("决定给予", "严重违纪违法"),
            ("审查调查", "涉嫌严重违纪违法"),
            ("监察调查", "涉嫌严重违纪违法"),
        ]
        for needle, label in ccdi_patterns:
            if needle in text:
                return label
        return "其他"
    audit_patterns = [
        ("会计师事务所", "审计执业违法"),
        ("利用未公开信息", "利用未公开信息交易"),
        ("经商办企业", "公职人员违规经商及兼职"),
        ("违规兼职取酬", "公职人员违规经商及兼职"),
        ("营利性活动", "公职人员违规经商及兼职"),
        ("违规入股", "公职人员违规经商及兼职"),
        ("违规经营投注站", "公职人员违规经商及兼职"),
        ("借用企业车辆", "公职人员违规经商及兼职"),
        ("八项规定", "违反中央八项规定"),
        ("公款旅游", "违反中央八项规定"),
        ("超标小轿车", "违反中央八项规定"),
        ("滥发礼品", "违反中央八项规定"),
        ("小金库", "违反财经纪律"),
        ("公款私存", "违反财经纪律"),
        ("违规使用项目经费", "违反财经纪律"),
        ("违规使用扶贫工作经费", "违反财经纪律"),
        ("违规使用备用金", "违反财经纪律"),
        ("违规列支", "违反财经纪律"),
        ("违规发放奖金", "违反财经纪律"),
        ("财政奖励", "骗取套取财政资金"),
        ("财政专项资金", "骗取套取财政资金"),
        ("涉农资金", "骗取套取财政资金"),
        ("补助资金", "骗取套取财政资金"),
        ("补贴资金", "骗取套取财政资金"),
        ("拆迁补偿", "骗取套取财政资金"),
        ("征收补偿", "骗取套取财政资金"),
        ("安置补偿", "骗取套取财政资金"),
        ("虚报冒领", "骗取套取财政资金"),
        ("收受", "受贿及利益输送"),
        ("利用职务便利", "职务谋利及利益输送"),
        ("利用职权", "职务谋利及利益输送"),
        ("为亲属", "职务谋利及利益输送"),
        ("为子女", "职务谋利及利益输送"),
        ("为配偶", "职务谋利及利益输送"),
        ("违规出借建筑工程资质", "工程建设管理违法"),
        ("工程建设中违规", "工程建设管理违法"),
        ("违规收费", "违规收费"),
        ("虚增业绩", "会计财务造假"),
        ("虚假合同", "会计财务造假"),
        ("违规处置资产", "国有资产及权益损失"),
        ("违规集资", "非法集资"),
        ("违规批准发放贷款", "信贷违法"),
        ("违规开展贷款业务", "信贷违法"),
        ("违规采购", "政府采购违法"),
        ("保障房", "保障性住房违法"),
        ("经济适用住房", "保障性住房违法"),
        ("廉租房", "保障性住房违法"),
        ("贷款诈骗", "金融诈骗"),
        ("合同诈骗", "金融诈骗"),
        ("信用证诈骗", "金融诈骗"),
        ("虚假按揭", "金融诈骗"),
        ("骗贷", "金融诈骗"),
        ("违规放贷", "信贷违法"),
        ("违规干预贷款", "信贷违法"),
        ("财政补贴", "骗取套取财政资金"),
        ("养老金", "社保基金违法"),
        ("医保基金", "社保基金违法"),
        ("倒卖进口原油", "资源能源违法"),
        ("楼堂馆所", "政府投资及建设管理违法"),
        ("农田", "土地资源违法"),
        ("高价采购", "政府采购违法"),
        ("出借公司资金", "违规资金管理"),
        ("未认真履职", "滥用职权及失职渎职"),
        ("履职不力", "滥用职权及失职渎职"),
        ("违规决策", "国有资产及权益损失"),
        ("扶贫资金", "骗取套取财政资金"),
        ("逃汇", "外汇违法"),
        ("受贿", "受贿及利益输送"),
        ("收受钱款", "受贿及利益输送"),
        ("利益输送", "受贿及利益输送"),
        ("贪污", "贪污侵占"),
        ("侵占", "贪污侵占"),
        ("挪用", "挪用资金"),
        ("骗取财政", "骗取套取财政资金"),
        ("骗取专项资金", "骗取套取财政资金"),
        ("套取财政", "骗取套取财政资金"),
        ("非法集资", "非法集资"),
        ("非法吸收公众存款", "非法集资"),
        ("骗取贷款", "信贷违法"),
        ("违规发放贷款", "信贷违法"),
        ("虚开增值税", "涉税违法"),
        ("偷逃税", "涉税违法"),
        ("滥用职权", "滥用职权及失职渎职"),
        ("玩忽职守", "滥用职权及失职渎职"),
        ("失职", "滥用职权及失职渎职"),
        ("国有权益损失", "国有资产及权益损失"),
        ("国有资产损失", "国有资产及权益损失"),
        ("土地", "土地资源违法"),
    ]
    patterns = [
        ("内幕交易", "内幕交易"),
        ("操纵", "操纵市场"),
        ("信息披露", "信息披露违法"),
        ("未按规定披露", "信息披露违法"),
        ("从业人员违规买卖", "从业人员违规买卖证券"),
        ("资产评估", "资产评估执业违法"),
        ("注册会计师", "审计执业违法"),
        ("会计师事务所", "审计执业违法"),
        ("政府采购", "政府采购违法"),
        ("会计法", "会计信息质量违法"),
    ]
    selected_patterns = (audit_patterns if source == "审计署" else []) + patterns
    if source == "审计署":
        title = text.split("\n", 1)[0]
        for needle, label in selected_patterns:
            if needle in title:
                return label
    for needle, label in selected_patterns:
        if needle in text:
            return label
    return "其他"


def extract_parties(text: str) -> str:
    patterns = [
        r"当\s*事\s*人[:：]\s*(.{1,180}?)(?=\s*地\s*址[:：]|住所[:：]|依据|依照|$)",
        r"当事人[:：]\s*(.{1,180}?)(?:。|\n|依据|依照|$)",
    ]
    parties = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.S):
            value = clean_text(match.group(1))
            value = re.sub(r"\s+", " ", value).strip(" ，,；;。")
            if value and value not in parties:
                parties.append(value)
        if parties:
            break
    return "；".join(parties[:8])


def extract_audit_parties(title: str) -> str:
    value = re.sub(r"^[一二三四五六七八九十百\d]+[、．.]\s*", "", clean_text(title))
    parts = re.split(
        r"涉嫌|未认真履职|履职不力|违规|贷款诈骗|合同诈骗|信用证诈骗|虚假按揭|高息转贷|逃汇|操纵|骗取|套取|非法|受贿|贪污|挪用|侵占|滥用|玩忽职守|失职|弄虚作假|损失浪费|故意销毁|造成|问题$|案件?$",
        value,
        maxsplit=1,
    )
    party = clean_text(parts[0]).strip("关于 ，,；;。")
    return party if 1 < len(party) <= 100 else clean_text(value[:100])


def classify_party(parties: str, text: str) -> tuple[str, str]:
    combined = f"{parties}\n{text[:1800]}"
    role = ""
    if re.search(r"签字注册会计师|注册会计师", combined):
        role = "注册会计师"
    elif re.search(r"签字资产评估师|资产评估师", combined):
        role = "资产评估师"
    elif "单位负责人" in combined:
        role = "单位负责人"
    elif "会计机构负责人" in combined:
        role = "会计机构负责人"
    elif "证券从业人员" in combined:
        role = "证券从业人员"

    if "会计师事务所" in parties:
        return "会计师事务所", role or "审计机构"
    if re.search(r"资产评估(?:有限|公司|事务所|机构)|评估有限公司", parties):
        return "企业/资产评估机构", role or "资产评估机构"
    if re.search(r"公司|集团|银行|证券|期货|基金|合伙企业|有限合伙|股份|厂|所", parties):
        return "企业", role
    if role == "注册会计师":
        return "注册会计师", role
    return "个人" if parties else "未识别", role


def classify_audit_party(parties: str, title: str) -> tuple[str, str]:
    combined = f"{parties}\n{title}"
    role_match = re.search(
        r"((?:原|时任)?(?:党组成员、?|党委书记、?|党委副书记、?|党委委员、?)?(?:副)?(?:董事长|总裁|总经理|经理|行长|局长|厅长|市长|区长|县长|处长|科长|股长|部长|主任|书记|主席|委员|负责人|法定代表人|会计|出纳|护士|工作人员))",
        combined,
    )
    role = role_match.group(1) if role_match else ""
    if "会计师事务所" in combined:
        return "会计师事务所", role or "审计机构"
    if "注册会计师" in combined:
        return "注册会计师", "注册会计师"
    if role or re.search(r"等人|有关人员|干部|个人|企业主", combined):
        return "个人", role
    if re.search(r"政府|委员会|委办|厅|局|中心|医院|学校|大学|学院|研究院|事业单位|相关单位|地区", parties):
        return "机关/事业单位", role
    if re.search(r"公司|集团|银行|企业|厂|合作社|基金|证券|信托|保险", parties):
        return "企业", role
    return "个人" if parties else "未识别", role


def extract_ccdi_parties(title: str) -> str:
    value = clean_text(title).lstrip("\ufeff\u200b")
    decision_match = re.search(
        r"(?:中共中央|中央纪委国家监委)?决定给予\s*(.{2,40}?)(?=开除|撤销|留党察看|党内)",
        value,
    )
    if decision_match:
        return clean_text(decision_match.group(1)).strip(" ，,；;。")
    party = re.split(
        r"涉嫌严重违纪违法|严重违纪违法|接受中央纪委国家监委纪律审查和监察调查|"
        r"接受纪律审查和监察调查|接受审查调查|接受监察调查|被[\"“”]?双开[\"“”]?|"
        r"被开除党籍和公职|被开除党籍|被开除公职|受到党纪政务处分",
        value,
        maxsplit=1,
    )[0]
    return clean_text(party).strip(" ，,；;。")[:120]


def extract_penalty_summary(text: str) -> str:
    match = re.search(
        r"((?:我会|财政部)决定[:：]?\s*.{1,520}?)(?=(?:上述当事人|你(?:公司|所|单位|个人)?应|如不服|财\s*政\s*部|中国证监会|$))",
        text,
        re.S,
    )
    if match:
        summary = clean_text(match.group(1))
        sentence_end = summary.find("。")
        if sentence_end != -1:
            summary = summary[: sentence_end + 1]
        return summary
    matches = re.findall(r"((?:给予|责令|没收|处以)[^。\n]{2,180}(?:行政处罚|罚款|违法所得|停业|从业))", text)
    return "；".join(clean_text(item) for item in matches[:4])


def extract_audit_summary(text: str) -> str:
    sentences = [clean_text(item) for item in re.findall(r"[^。；\n]+(?:[。；]|$)", text) if clean_text(item)]
    outcome_pattern = re.compile(
        r"判处|判决|裁定|给予|处分|开除|撤职|免职|降级|刑事拘留|逮捕|立案|移送.*机关|"
        r"罚款|罚金|追缴|收缴|没收|退缴|追回|挽回|整改|行政处罚|解除劳动|取消资格"
    )
    matches = [sentence for sentence in sentences if outcome_pattern.search(sentence)]
    substantive = [sentence for sentence in matches if not re.search(r"审计署将.*移送", sentence)]
    selected = substantive[-4:] if substantive else matches[-3:]
    if not selected:
        selected = sentences[-2:]
    summary = "".join(selected)
    return summary[:700]


def extract_ccdi_summary(text: str, title: str, case_stage: str) -> str:
    sentences = [clean_text(item) for item in re.findall(r"[^。；\n]+(?:[。；]|$)", text) if clean_text(item)]
    if case_stage == "执纪审查":
        matches = [sentence for sentence in sentences if re.search(r"接受.*(?:审查|监察).*调查", sentence)]
        return (matches[-1] if matches else title)[:700]
    outcome_pattern = re.compile(
        r"决定给予|开除党籍|开除公职|取消.*待遇|收缴.*所得|移送检察机关|"
        r"免职|撤销.*职务|留党察看|党内(?:严重)?警告|政务处分"
    )
    matches = [sentence for sentence in sentences if outcome_pattern.search(sentence)]
    return ("".join(matches[-3:]) if matches else title)[:700]


def audit_summary_override(record: dict) -> str:
    summaries = AUDIT_SUMMARY_OVERRIDES.get(record.get("file_no", ""), [])
    match = re.search(r"#case-(\d+)$", record.get("url", ""))
    case_index = int(match.group(1)) if match else 0
    return summaries[case_index - 1] if 0 < case_index <= len(summaries) else ""


def extract_penalty_types(text: str) -> str:
    checks = [
        ("刑事判决", r"判处[^。；\n]*(?:有期徒刑|无期徒刑|死刑|拘役)|刑事处罚"),
        ("党纪政务处分", r"开除党籍|开除公职|党内警告|党内严重警告|留党察看|政务警告|行政撤职|行政记过|记大过|降级|撤职|撤销党内职务|诫勉谈话|纪律处分|政务处分|党纪政纪处分|处理处分"),
        ("罚金", r"罚金"),
        ("追缴/收缴/没收", r"追缴|收缴|退缴|退还|收回|没收[^。；\n]{0,20}(?:财产|赃款|违法所得|非法所得)"),
        ("补缴税款", r"补缴税款"),
        ("公开通报/问责", r"公开通报|监督问责"),
        ("免职/解除任用", r"免职|解聘|解除劳动|取消.*资格"),
        ("移送司法机关", r"移送(?:司法|公安|检察|纪检监察|有关)机关|移送.*调查处理"),
        ("整改/挽回损失", r"整改|追回|挽回(?:经济)?损失|退回.*资金"),
        ("警告", r"警告"),
        ("罚款", r"罚款|处以[^。；\n]*?元"),
        ("没收违法所得", r"没收[^。；\n]*违法所得"),
        ("责令停业", r"责令停业|暂停经营|停业"),
        ("暂停/停止从业", r"暂停从业|停止从业|责令停止从业|暂停执业"),
        ("责令改正", r"责令改正"),
        ("市场禁入", r"市场禁入|禁入"),
        ("吊销/撤销资格", r"吊销|撤销.*资格|撤销.*许可"),
        ("通报批评", r"通报批评"),
    ]
    found = []
    for label, pattern in checks:
        if re.search(pattern, text) and label not in found:
            found.append(label)
    if "追缴/收缴/没收" in found and "没收违法所得" in found:
        found.remove("没收违法所得")
    return "；".join(found) if found else "其他"


def extract_ccdi_penalty_types(text: str, case_stage: str) -> str:
    if case_stage == "执纪审查":
        return "接受审查调查"
    checks = [
        ("开除党籍", r"开除党籍|双开"),
        ("开除公职", r"开除(?:其)?公职|开除党籍和公职|双开"),
        ("取消待遇", r"取消[^。；\n]{0,30}待遇"),
        ("收缴违纪违法所得", r"收缴[^。；\n]{0,30}(?:违纪|违法)所得"),
        ("移送检察机关", r"移送检察机关|移送司法机关"),
        ("撤销党内职务", r"撤销党内职务"),
        ("留党察看", r"留党察看"),
        ("党内严重警告", r"党内严重警告"),
        ("党内警告", r"党内警告"),
        ("政务撤职", r"政务撤职|行政撤职"),
        ("免职", r"免职"),
    ]
    found = [label for label, pattern in checks if re.search(pattern, text)]
    return "；".join(found) if found else "党纪政务处分"


def enrich_case(record: dict, ordinal: int) -> dict:
    body = clean_text(record.get("body", ""))
    record["body"] = body
    record["case_id"] = record.get("case_id") or f"{record['source']}-{ordinal:05d}"
    if record["source"] == "审计署":
        record["parties"] = extract_audit_parties(record["title"])
        record["party_type"], record["party_role"] = classify_audit_party(record["parties"], record["title"])
        record["penalty_summary"] = audit_summary_override(record) or extract_audit_summary(body)
        penalty_text = record["penalty_summary"] + "\n" + body
    elif record["source"] == CCDI_SOURCE:
        record["parties"] = extract_ccdi_parties(record["title"])
        record["party_type"] = "个人"
        record["party_role"] = ""
        record["penalty_summary"] = extract_ccdi_summary(
            body,
            record["title"],
            record.get("case_stage", record.get("category", "")),
        )
        penalty_text = record["penalty_summary"] + "\n" + body
    else:
        record["parties"] = extract_parties(body)
        record["party_type"], record["party_role"] = classify_party(record["parties"], body)
        record["penalty_summary"] = extract_penalty_summary(body)
        penalty_text = record["penalty_summary"] + "\n" + body
    record["violation_type"] = detect_violation_type(record["title"] + "\n" + body, record["source"])
    if record["source"] == CCDI_SOURCE:
        record["penalty_types"] = extract_ccdi_penalty_types(
            penalty_text,
            record.get("case_stage", record.get("category", "")),
        )
    else:
        record["penalty_types"] = extract_penalty_types(penalty_text)
    record["summary"] = clean_text(body[:260])
    record["sha256"] = hashlib.sha256((record["url"] + "\n" + body).encode("utf-8")).hexdigest()
    return record


def markdown_for_case(record: dict) -> str:
    frontmatter = {
        "case_id": record["case_id"],
        "source": record["source"],
        "agency": record.get("agency", ""),
        "department": record.get("department", ""),
        "category": record.get("category", ""),
        "title": record["title"],
        "file_no": record.get("file_no", ""),
        "publish_date": record.get("publish_date", ""),
        "parties": record.get("parties", ""),
        "party_type": record.get("party_type", ""),
        "party_role": record.get("party_role", ""),
        "case_stage": record.get("case_stage", ""),
        "cadre_level": record.get("cadre_level", ""),
        "violation_type": record.get("violation_type", ""),
        "penalty_types": record.get("penalty_types", ""),
        "source_url": record["url"],
        "sha256": record["sha256"],
    }
    if record.get("case_topic"):
        frontmatter["case_topic"] = record["case_topic"]
    lines = ["---"]
    for key, value in frontmatter.items():
        value = str(value).replace('"', '\\"')
        lines.append(f'{key}: "{value}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {record['title']}")
    lines.append("")
    lines.append(f"- 来源：{record['source']}")
    lines.append(f"- 文号：{record.get('file_no', '')}")
    lines.append(f"- 发布日期：{record.get('publish_date', '')}")
    if record.get("parties"):
        lines.append(f"- 当事人：{record['parties']}")
    if record.get("party_type"):
        lines.append(f"- 主体类型：{record['party_type']}")
    if record.get("party_role"):
        lines.append(f"- 责任身份：{record['party_role']}")
    if record.get("case_stage"):
        lines.append(f"- 通告阶段：{record['case_stage']}")
    if record.get("cadre_level"):
        lines.append(f"- 干部层级：{record['cadre_level']}")
    if record.get("case_topic"):
        lines.append(f"- 案例专题：{record['case_topic']}")
    lines.append(f"- 案由分类：{record.get('violation_type', '')}")
    if record.get("penalty_types"):
        lines.append(f"- 处罚/处理类型：{record['penalty_types']}")
    if record.get("penalty_summary"):
        lines.append(f"- 处罚/处理摘要：{record['penalty_summary']}")
    lines.append(f"- 原文链接：{record['url']}")
    lines.append("")
    lines.append("## 正文")
    lines.append("")
    lines.append(record["body"])
    lines.append("")
    return "\n".join(lines)


def write_kb(records: list[dict], output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "markdown").mkdir(parents=True, exist_ok=True)
    (output_dir / "raw_html").mkdir(parents=True, exist_ok=True)
    for record in records:
        source_dir = safe_filename(record["source"], 24)
        stem = safe_filename(f"{record['case_id']}_{record['publish_date']}_{record['file_no']}_{record['title']}", 140)
        md_path = output_dir / "markdown" / source_dir / f"{stem}.md"
        raw_path = output_dir / "raw_html" / source_dir / f"{stem}.html"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = markdown_for_case(record)
        md_path.write_text(markdown, encoding="utf-8", newline="\n")
        raw_path.write_text(record.get("raw_html", ""), encoding="utf-8", newline="\n")
        record["markdown_path"] = md_path.relative_to(output_dir).as_posix()
        record["raw_html_path"] = raw_path.relative_to(output_dir).as_posix()
    write_index(output_dir, records)
    write_sqlite(output_dir / "penalty_cases.sqlite", records)
    write_search_script(output_dir)
    write_readme(output_dir, records)


def write_index(output_dir: Path, records: list[dict]) -> None:
    with (output_dir / "index.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            item = {key: value for key, value in record.items() if key not in {"body", "raw_html"}}
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "documents": len(records),
        "by_source": dict(Counter(record["source"] for record in records)),
        "by_violation_type": dict(Counter(record.get("violation_type", "其他") for record in records)),
        "by_case_topic": dict(Counter(record.get("case_topic", "其他") for record in records)),
        "sources": {
            "财政部": MOF_LIST_URL,
            "证监会": f"https://www.csrc.gov.cn/csrc/c101928/zfxxgk_zdgk.shtml",
            "审计署": AUDIT_LIST_URL,
            CCDI_SOURCE: "https://www.ccdi.gov.cn/scdcn/",
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_sqlite(db_path: Path, records: list[dict]) -> None:
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE cases (
              id INTEGER PRIMARY KEY,
              case_id TEXT UNIQUE,
              source TEXT,
              agency TEXT,
              title TEXT,
              file_no TEXT,
              publish_date TEXT,
              parties TEXT,
              party_type TEXT,
              party_role TEXT,
              case_stage TEXT,
              cadre_level TEXT,
              violation_type TEXT,
              penalty_types TEXT,
              penalty_summary TEXT,
              url TEXT,
              markdown_path TEXT,
              sha256 TEXT
            )
            """
        )
        connection.execute(
            "CREATE VIRTUAL TABLE cases_fts USING fts5(title, file_no, parties, party_type, party_role, violation_type, penalty_types, penalty_summary, body, url, tokenize='unicode61')"
        )
        for record in records:
            cursor = connection.execute(
                """
                INSERT INTO cases
                (case_id, source, agency, title, file_no, publish_date, parties, party_type, party_role, case_stage, cadre_level, violation_type, penalty_types, penalty_summary, url, markdown_path, sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["case_id"],
                    record["source"],
                    record.get("agency", ""),
                    record["title"],
                    record.get("file_no", ""),
                    record.get("publish_date", ""),
                    record.get("parties", ""),
                    record.get("party_type", ""),
                    record.get("party_role", ""),
                    record.get("case_stage", ""),
                    record.get("cadre_level", ""),
                    record.get("violation_type", ""),
                    record.get("penalty_types", ""),
                    record.get("penalty_summary", ""),
                    record["url"],
                    record.get("markdown_path", ""),
                    record["sha256"],
                ),
            )
            connection.execute(
                "INSERT INTO cases_fts (rowid, title, file_no, parties, party_type, party_role, violation_type, penalty_types, penalty_summary, body, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    record["title"],
                    record.get("file_no", ""),
                    record.get("parties", ""),
                    record.get("party_type", ""),
                    record.get("party_role", ""),
                    record.get("violation_type", ""),
                    record.get("penalty_types", ""),
                    record.get("penalty_summary", ""),
                    record.get("body", ""),
                    record["url"],
                ),
            )
        connection.commit()
    finally:
        connection.close()


def write_search_script(output_dir: Path) -> None:
    script = '''import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Search local regulatory and discipline cases.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--db", default=str(Path(__file__).with_name("penalty_cases.sqlite")))
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    try:
        columns = [
            "c.source", "c.title", "c.file_no", "c.parties", "c.party_type", "c.party_role",
            "c.case_stage", "c.cadre_level", "c.violation_type", "c.penalty_types",
            "c.penalty_summary", "cases_fts.body",
        ]
        terms = [term for term in args.query.split() if term] or [args.query]
        clauses = []
        params = []
        for term in terms:
            clauses.append("(" + " OR ".join(f"{column} LIKE ?" for column in columns) + ")")
            params.extend([f"%{term}%"] * len(columns))
        sql = f"""
            SELECT c.source, c.title, c.file_no, c.publish_date, c.parties, c.party_type, c.party_role, c.violation_type, c.penalty_types,
                   c.markdown_path, c.url, COALESCE(NULLIF(c.penalty_summary, ''), substr(cases_fts.body, 1, 220))
            FROM cases_fts
            JOIN cases c ON c.id = cases_fts.rowid
            WHERE {" AND ".join(clauses)}
            ORDER BY c.publish_date DESC, c.id DESC
            LIMIT ?
            """
        rows = connection.execute(sql, (*params, args.limit)).fetchall()
    finally:
        connection.close()

    for index, row in enumerate(rows, start=1):
        source, title, file_no, publish_date, parties, party_type, party_role, violation_type, penalty_types, path, url, snippet = row
        print(f"{index}. [{source}] {title}")
        print(f"   {file_no} | {publish_date} | {violation_type}")
        if parties:
            print(f"   当事人：{parties}")
        print(f"   主体类型：{party_type or '未识别'}" + (f" | 责任身份：{party_role}" if party_role else ""))
        print(f"   处罚/处理类型：{penalty_types or '未识别'}")
        print(f"   {path}")
        print(f"   {url}")
        if snippet:
            print(f"   {snippet}")


if __name__ == "__main__":
    main()
'''
    (output_dir / "search_penalty_cases.py").write_text(script, encoding="utf-8")


def write_readme(output_dir: Path, records: list[dict]) -> None:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    readme = f"""# 财政监管与纪检监察案例库

生成时间：{manifest["generated_at"]}

## 内容

- `markdown/`：按来源拆分的 Markdown 正文和元数据。
- `raw_html/`：抓取时保留的原始 HTML 或正文 HTML。
- `penalty_cases.sqlite`：SQLite + FTS5 全文索引。
- `index.jsonl`：每个案例一行的元数据索引。
- `manifest.json`：抓取统计和来源。
- `search_penalty_cases.py`：命令行检索工具。

## 统计

- 总案例：{len(records)}
- 财政部：{sum(1 for record in records if record["source"] == "财政部")}
- 证监会：{sum(1 for record in records if record["source"] == "证监会")}
- 审计署：{sum(1 for record in records if record["source"] == "审计署")}
- 中央纪委国家监委：{sum(1 for record in records if record["source"] == CCDI_SOURCE)}

## 检索示例

```powershell
python search_penalty_cases.py "内幕交易"
python search_penalty_cases.py "资产评估 警告" --limit 20
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")


def html_path(record: dict) -> str:
    return Path(record["markdown_path"]).with_suffix(".html").as_posix().replace("markdown/", "")


def write_site(records: list[dict], kb_dir: Path, site_dir: Path) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    (site_dir / "assets").mkdir(parents=True, exist_ok=True)
    for record in records:
        path = site_dir / html_path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case_page(record), encoding="utf-8", newline="\n")
        record["html_path"] = html_path(record)
    (site_dir / "index.html").write_text(index_page(records, kb_dir), encoding="utf-8", newline="\n")
    (site_dir / "assets" / "site.css").write_text(site_css(), encoding="utf-8", newline="\n")
    (site_dir / "site_manifest.json").write_text(
        json.dumps({"documents": len(records), "generated_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_existing_cases(kb_dir: Path) -> list[dict]:
    records = []
    index_path = kb_dir / "index.jsonl"
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        markdown_path = kb_dir / record["markdown_path"]
        markdown = markdown_path.read_text(encoding="utf-8")
        body_marker = "\n## 正文\n"
        record["body"] = markdown.split(body_marker, 1)[1].strip() if body_marker in markdown else ""
        raw_path = kb_dir / record.get("raw_html_path", "")
        record["raw_html"] = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""
        records.append(record)
    return records


def case_page(record: dict) -> str:
    body = "\n".join(f"<p>{html.escape(part)}</p>" for part in record["body"].split("\n\n") if part.strip())
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(record["title"])} | 处罚案例库</title>
  <link rel="stylesheet" href="../assets/site.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="../index.html">处罚案例库</a>
    <form class="top-search" action="../index.html">
      <input name="q" placeholder="搜索案由、当事人、文号、处罚或处理结果" autocomplete="off">
      <button type="submit">搜索</button>
    </form>
  </header>
  <main class="doc-layout">
    <article class="doc">
      <h1>{html.escape(record["title"])}</h1>
      <div class="meta-row">
        <span>{html.escape(record.get("source", ""))}</span>
        <span>{html.escape(record.get("file_no", ""))}</span>
        <span>{html.escape(record.get("publish_date", ""))}</span>
        <span>{html.escape(record.get("violation_type", ""))}</span>
        <span>{html.escape(record.get("penalty_types", ""))}</span>
      </div>
      {body}
    </article>
    <aside class="side">
      <a class="side-link" href="../index.html">返回首页</a>
      <a class="side-link" href="{html.escape(record["url"])}" target="_blank" rel="noreferrer">原文链接</a>
      <div class="source-note">
        <strong>当事人</strong><br>{html.escape(record.get("parties", "") or "未自动识别")}
      </div>
      <div class="source-note">
        <strong>主体类型</strong><br>{html.escape(record.get("party_type", "") or "未自动识别")}
        {("<br><strong>责任身份</strong><br>" + html.escape(record.get("party_role", ""))) if record.get("party_role") else ""}
      </div>
      {(f'<div class="source-note"><strong>通告阶段</strong><br>{html.escape(record.get("case_stage", ""))}<br><strong>干部层级</strong><br>{html.escape(record.get("cadre_level", ""))}</div>') if record.get("case_stage") else ""}
      <div class="source-note">
        <strong>处罚/处理类型</strong><br>{html.escape(record.get("penalty_types", "") or "未自动识别")}
      </div>
      <div class="source-note">
        <strong>处罚/处理摘要</strong><br>{html.escape(record.get("penalty_summary", "") or "未自动识别")}
      </div>
    </aside>
  </main>
</body>
</html>
"""


def index_page(records: list[dict], kb_dir: Path) -> str:
    manifest = json.loads((kb_dir / "manifest.json").read_text(encoding="utf-8"))
    search_index = [
        {
            "title": record["title"],
            "source": record["source"],
            "file_no": record.get("file_no", ""),
            "publish_date": record.get("publish_date", ""),
            "parties": record.get("parties", ""),
            "party_type": record.get("party_type", ""),
            "party_role": record.get("party_role", ""),
            "case_stage": record.get("case_stage", ""),
            "cadre_level": record.get("cadre_level", ""),
            "violation_type": record.get("violation_type", ""),
            "penalty_types": record.get("penalty_types", ""),
            "penalty_summary": record.get("penalty_summary", ""),
            "summary": record.get("summary", ""),
            "html_path": record["html_path"],
            "search": clean_text(
                " ".join(
                    [
                        record["title"],
                        record.get("file_no", ""),
                        record.get("publish_date", ""),
                        record.get("parties", ""),
                        record.get("party_type", ""),
                        record.get("party_role", ""),
                        record.get("case_stage", ""),
                        record.get("cadre_level", ""),
                        record.get("violation_type", ""),
                        record.get("penalty_types", ""),
                        record.get("penalty_summary", ""),
                        record.get("body", "")[:2000],
                    ]
                )
            ),
        }
        for record in records
    ]
    data = json.dumps(search_index, ensure_ascii=False, separators=(",", ":"))
    source_cards = "\n".join(
        f'<button class="filter-chip source-chip" type="button" data-source="{html.escape(source)}" aria-pressed="false"><span>{html.escape(source)}</span><strong>{count}</strong></button>'
        for source, count in manifest.get("by_source", {}).items()
    )
    party_counts = Counter(record.get("party_type") or "未识别" for record in records)
    party_labels = {"企业/资产评估机构": "资产评估机构"}
    party_cards = "\n".join(
        f'<button class="filter-chip party-chip" type="button" data-party="{html.escape(name)}" aria-pressed="false"><span>{html.escape(party_labels.get(name, name))}</span><strong>{count}</strong></button>'
        for name, count in party_counts.most_common()
    )
    penalty_counts = Counter()
    for record in records:
        for penalty_type in str(record.get("penalty_types") or "未识别").split("；"):
            penalty_type = penalty_type.strip()
            if penalty_type:
                penalty_counts[penalty_type] += 1
    penalty_cards = "\n".join(
        f'<button class="filter-chip penalty-chip" type="button" data-penalty="{html.escape(name)}" aria-pressed="false"><span>{html.escape(name)}</span><strong>{count}</strong></button>'
        for name, count in penalty_counts.most_common()
    )
    stage_counts = Counter(record.get("case_stage") for record in records if record.get("case_stage"))
    stage_cards = "\n".join(
        f'<button class="filter-chip stage-chip" type="button" data-stage="{html.escape(name)}" aria-pressed="false"><span>{html.escape(name)}</span><strong>{count}</strong></button>'
        for name, count in stage_counts.most_common()
    )
    cadre_counts = Counter(record.get("cadre_level") for record in records if record.get("cadre_level"))
    cadre_cards = "\n".join(
        f'<button class="filter-chip cadre-chip" type="button" data-cadre="{html.escape(name)}" aria-pressed="false"><span>{html.escape(name)}</span><strong>{count}</strong></button>'
        for name, count in cadre_counts.most_common()
    )
    type_cards = "\n".join(
        f'<button class="filter-chip type-chip" type="button" data-type="{html.escape(name)}" aria-pressed="false"><span>{html.escape(name)}</span><strong>{count}</strong></button>'
        for name, count in sorted(manifest.get("by_violation_type", {}).items(), key=lambda item: item[1], reverse=True)[:12]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>财政监管与纪检监察案例库</title>
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header class="hero">
    <div>
      <h1>财政监管与纪检监察案例库</h1>
      <p>离线整理行政处罚决定、审计移送问题线索查处通报，以及中央层级审查调查与党纪政务处分通告。</p>
    </div>
    <div class="stat">{len(records)}<span>案例</span></div>
  </header>
  <main class="home">
    <section class="search-panel">
      <div class="search-row">
        <input id="searchInput" placeholder="输入关键词，例如：内幕交易、资产评估、财监法、信息披露" autofocus>
        <button id="clearFilter" type="button">重置</button>
      </div>
      <div class="filter-groups" aria-label="案例筛选">
        <section class="filter-group" aria-labelledby="sourceFilterLabel">
          <h2 id="sourceFilterLabel">发布机构</h2>
          <div class="filter-options">{source_cards}</div>
        </section>
        <section class="filter-group" aria-labelledby="partyFilterLabel">
          <h2 id="partyFilterLabel">被处理主体</h2>
          <div class="filter-options">{party_cards}</div>
        </section>
        <section class="filter-group" aria-labelledby="penaltyFilterLabel">
          <h2 id="penaltyFilterLabel">处罚/处理类型</h2>
          <div class="filter-options">{penalty_cards}</div>
        </section>
        {f'''<section class="filter-group" aria-labelledby="stageFilterLabel">
          <h2 id="stageFilterLabel">通告阶段</h2>
          <div class="filter-options">{stage_cards}</div>
        </section>''' if stage_cards else ''}
        {f'''<section class="filter-group" aria-labelledby="cadreFilterLabel">
          <h2 id="cadreFilterLabel">干部层级</h2>
          <div class="filter-options">{cadre_cards}</div>
        </section>''' if cadre_cards else ''}
        <details class="more-filters">
          <summary>按案由进一步筛选</summary>
          <div class="filter-options">{type_cards}</div>
        </details>
      </div>
      <div id="searchMeta" class="search-meta" aria-live="polite"></div>
      <div id="results" class="results"></div>
    </section>
  </main>
  <script>
  const DATA = {data};
  const input = document.getElementById('searchInput');
  const results = document.getElementById('results');
  const meta = document.getElementById('searchMeta');
  const clearFilter = document.getElementById('clearFilter');
  const initialParams = new URLSearchParams(location.search);
  let savedState = {{}};
  try {{ savedState = JSON.parse(sessionStorage.getItem('caseFilters') || '{{}}'); }} catch (_) {{}}
  let activeSource = initialParams.get('source') || savedState.source || '';
  let activeType = initialParams.get('type') || savedState.type || '';
  let activeParty = initialParams.get('party') || savedState.party || '';
  let activePenalty = initialParams.get('penalty') || savedState.penalty || '';
  let activeStage = initialParams.get('stage') || savedState.stage || '';
  let activeCadre = initialParams.get('cadre') || savedState.cadre || '';
  input.value = initialParams.get('q') || savedState.q || '';

  function escapeHtml(value) {{
    return String(value || '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
  }}

  function snippet(text, terms) {{
    const value = text || '';
    const lower = value.toLowerCase();
    let pos = -1;
    for (const term of terms) {{
      const found = lower.indexOf(term.toLowerCase());
      if (found !== -1 && (pos === -1 || found < pos)) pos = found;
    }}
    if (pos === -1) return value.slice(0, 150);
    const start = Math.max(0, pos - 44);
    return (start ? '...' : '') + value.slice(start, start + 180) + (start + 180 < value.length ? '...' : '');
  }}

  function chip(value) {{
    return value ? `<span>${{escapeHtml(value)}}</span>` : '';
  }}

  function render() {{
    const query = input.value.trim();
    const terms = query ? query.split(/\\s+/).filter(Boolean) : [];
    let matches = DATA;
    if (activeSource) matches = matches.filter(item => item.source === activeSource);
    if (activeType) matches = matches.filter(item => item.violation_type === activeType);
    if (activeParty) matches = matches.filter(item => (item.party_type || '未识别') === activeParty);
    if (activePenalty) matches = matches.filter(item =>
      String(item.penalty_types || '未识别').split('；').map(value => value.trim()).includes(activePenalty)
    );
    if (activeStage) matches = matches.filter(item => item.case_stage === activeStage);
    if (activeCadre) matches = matches.filter(item => item.cadre_level === activeCadre);
    if (terms.length) {{
      matches = matches.filter(item => {{
        const haystack = item.search.toLowerCase();
        return terms.every(term => haystack.includes(term.toLowerCase()));
      }});
    }}
    const shown = matches.slice(0, 100);
    meta.textContent = `找到 ${{matches.length}} 条结果${{matches.length > shown.length ? '，显示前 100 条' : ''}}`;
    results.innerHTML = shown.map(item => `
      <a class="result" href="${{item.html_path}}">
        <div class="result-title">${{escapeHtml(item.title)}}</div>
        <div class="result-meta">
          ${{chip(item.source)}}
          ${{chip(item.file_no)}}
          ${{chip(item.publish_date)}}
          ${{chip(item.violation_type)}}
          ${{chip(item.penalty_types)}}
          ${{chip(item.party_type ? '主体 ' + item.party_type : '')}}
          ${{chip(item.party_role ? '身份 ' + item.party_role : '')}}
          ${{chip(item.case_stage ? '阶段 ' + item.case_stage : '')}}
          ${{chip(item.cadre_level ? '层级 ' + item.cadre_level : '')}}
          ${{chip(item.parties ? '当事人 ' + item.parties : '')}}
        </div>
        <p>${{escapeHtml(snippet(item.penalty_summary || item.summary || item.search, terms))}}</p>
      </a>
    `).join('');
    const state = new URLSearchParams();
    if (query) state.set('q', query);
    if (activeSource) state.set('source', activeSource);
    if (activeType) state.set('type', activeType);
    if (activeParty) state.set('party', activeParty);
    if (activePenalty) state.set('penalty', activePenalty);
    if (activeStage) state.set('stage', activeStage);
    if (activeCadre) state.set('cadre', activeCadre);
    sessionStorage.setItem('caseFilters', JSON.stringify({{
      q: query, source: activeSource, type: activeType, party: activeParty,
      penalty: activePenalty, stage: activeStage, cadre: activeCadre
    }}));
    try {{ history.replaceState(null, '', 'index.html' + (state.toString() ? '?' + state.toString() : '')); }} catch (_) {{}}
  }}

  clearFilter.addEventListener('click', () => {{
    activeSource = '';
    activeType = '';
    activeParty = '';
    activePenalty = '';
    activeStage = '';
    activeCadre = '';
    input.value = '';
    document.querySelectorAll('.filter-chip').forEach(button => {{
      button.classList.remove('active');
      button.setAttribute('aria-pressed', 'false');
    }});
    render();
  }});
  document.querySelectorAll('.source-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activeSource = activeSource === button.dataset.source ? '' : button.dataset.source;
      document.querySelectorAll('.source-chip').forEach(item => {{
        const selected = item.dataset.source === activeSource;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  document.querySelectorAll('.type-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activeType = activeType === button.dataset.type ? '' : button.dataset.type;
      document.querySelectorAll('.type-chip').forEach(item => {{
        const selected = item.dataset.type === activeType;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  document.querySelectorAll('.party-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activeParty = activeParty === button.dataset.party ? '' : button.dataset.party;
      document.querySelectorAll('.party-chip').forEach(item => {{
        const selected = item.dataset.party === activeParty;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  document.querySelectorAll('.penalty-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activePenalty = activePenalty === button.dataset.penalty ? '' : button.dataset.penalty;
      document.querySelectorAll('.penalty-chip').forEach(item => {{
        const selected = item.dataset.penalty === activePenalty;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  document.querySelectorAll('.stage-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activeStage = activeStage === button.dataset.stage ? '' : button.dataset.stage;
      document.querySelectorAll('.stage-chip').forEach(item => {{
        const selected = item.dataset.stage === activeStage;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  document.querySelectorAll('.cadre-chip').forEach(button => {{
    button.addEventListener('click', () => {{
      activeCadre = activeCadre === button.dataset.cadre ? '' : button.dataset.cadre;
      document.querySelectorAll('.cadre-chip').forEach(item => {{
        const selected = item.dataset.cadre === activeCadre;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-pressed', String(selected));
      }});
      render();
    }});
  }});
  input.addEventListener('input', render);
  document.querySelectorAll('.source-chip').forEach(item => item.classList.toggle('active', item.dataset.source === activeSource));
  document.querySelectorAll('.type-chip').forEach(item => item.classList.toggle('active', item.dataset.type === activeType));
  document.querySelectorAll('.party-chip').forEach(item => item.classList.toggle('active', item.dataset.party === activeParty));
  document.querySelectorAll('.penalty-chip').forEach(item => item.classList.toggle('active', item.dataset.penalty === activePenalty));
  document.querySelectorAll('.stage-chip').forEach(item => item.classList.toggle('active', item.dataset.stage === activeStage));
  document.querySelectorAll('.cadre-chip').forEach(item => item.classList.toggle('active', item.dataset.cadre === activeCadre));
  render();
  </script>
</body>
</html>
"""


def site_css() -> str:
    return """*{box-sizing:border-box}body{margin:0;background:#f6f4ef;color:#1f2933;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.65}.hero{min-height:220px;display:flex;align-items:end;justify-content:space-between;gap:28px;padding:42px 6vw 32px;background:#12343b;color:#fff}.hero h1{margin:0 0 10px;font-size:34px;letter-spacing:0}.hero p{margin:0;max-width:760px;color:#dce7e7}.stat{font-size:48px;font-weight:750;text-align:right}.stat span{display:block;font-size:14px;color:#bfd0d0}.home{max-width:1180px;margin:0 auto;padding:28px 20px 56px}.search-panel{display:block}.search-row{display:flex;gap:10px;margin-bottom:20px}.search-row input,.top-search input{width:100%;border:1px solid #cbd5d8;border-radius:6px;padding:12px 14px;font-size:15px;background:#fff}.search-row button,.top-search button{border:0;border-radius:6px;background:#c75146;color:#fff;padding:0 18px;font-size:15px;cursor:pointer;white-space:nowrap}.filter-groups{border-top:1px solid #d9dedc}.filter-group{display:grid;grid-template-columns:96px minmax(0,1fr);gap:14px;padding:15px 0;border-bottom:1px solid #d9dedc}.filter-group h2{margin:7px 0 0;font-size:14px;line-height:1.5;color:#56666d}.filter-options{display:flex;flex-wrap:wrap;gap:8px}.filter-chip{border:1px solid #cad4d5;background:#fff;border-radius:6px;padding:7px 10px;cursor:pointer;color:#21313a;font-size:14px;line-height:1.5}.filter-chip strong{margin-left:7px;color:#c75146;font-size:12px}.filter-chip:hover{border-color:#8aa1a6}.filter-chip.active{background:#12343b;color:#fff;border-color:#12343b}.filter-chip.active strong{color:#f5c7bd}.more-filters{padding:14px 0;border-bottom:1px solid #d9dedc}.more-filters summary{width:max-content;cursor:pointer;color:#315e67;font-size:14px}.more-filters .filter-options{margin-top:12px}.search-meta{margin:18px 0 12px;color:#5f6f76}.results{display:grid;gap:10px}.result{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid #d9dedc;border-radius:8px;padding:16px 18px}.result:hover{border-color:#8aa1a6}.result-title{font-weight:700;font-size:17px}.result-meta,.meta-row{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0;color:#56666d}.result-meta span,.meta-row span{background:#eef2f1;border-radius:999px;padding:2px 9px;font-size:13px}.result p{margin:8px 0 0;color:#44545b}.topbar{height:58px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:0 4vw;background:#12343b;color:#fff}.brand{color:#fff;text-decoration:none;font-weight:750}.top-search{display:flex;gap:8px;width:min(560px,60vw)}.top-search input{padding:8px 10px}.doc-layout{max-width:1180px;margin:0 auto;padding:28px 20px 64px;display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:28px}.doc{background:#fff;border:1px solid #d9dedc;border-radius:8px;padding:30px}.doc h1{font-size:28px;line-height:1.3;margin:0 0 12px}.doc p{margin:0 0 14px}.side{align-self:start;position:sticky;top:18px}.side-link{display:block;text-decoration:none;color:#12343b;background:#fff;border:1px solid #d9dedc;border-radius:6px;padding:10px 12px;margin-bottom:10px}.source-note{background:#fff;border:1px solid #d9dedc;border-radius:8px;padding:14px;margin-bottom:10px;color:#46565d}@media(max-width:820px){.hero{display:block}.stat{text-align:left;margin-top:18px}.search-row,.top-search{width:100%}.filter-group{display:block}.filter-group h2{margin:0 0 9px}.doc-layout{display:block}.doc{padding:20px}.side{position:static;margin-top:16px}.topbar{height:auto;align-items:flex-start;flex-direction:column;padding:14px 20px}.top-search{max-width:none}}"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local regulatory and discipline case libraries.")
    parser.add_argument("--mof-limit", type=int, default=None, help="limit MOF cases for testing")
    parser.add_argument("--csrc-limit", type=int, default=None, help="limit CSRC cases for testing")
    parser.add_argument("--audit-limit", type=int, default=None, help="limit Audit Office cases for testing")
    parser.add_argument("--audit-only", action="store_true", help="replace Audit Office cases while preserving existing MOF/CSRC cases")
    parser.add_argument("--ccdi-limit", type=int, default=None, help="limit CCDI/NSC notices for testing")
    parser.add_argument("--ccdi-pages", type=int, default=1, help="recent list pages to fetch per CCDI/NSC channel")
    parser.add_argument("--ccdi-detail-limit", type=int, default=0, help="fetch full text for the newest CCDI/NSC notices")
    parser.add_argument("--ccdi-only", action="store_true", help="replace CCDI/NSC notices while preserving the other sources")
    parser.add_argument("--skip-site", action="store_true")
    parser.add_argument("--site-only", action="store_true", help="rebuild the static site from the existing knowledge base")
    parser.add_argument("--repack-existing", action="store_true", help="rewrite the existing knowledge base with current filename and site rules")
    args = parser.parse_args()

    if args.site_only:
        records = load_existing_cases(ROOT)
        write_site(records, ROOT, SITE)
        print(f"Done. Rebuilt site for {len(records)} existing cases.")
        return

    if args.repack_existing:
        records = load_existing_cases(ROOT)
        write_kb(records, ROOT)
        write_site(records, ROOT, SITE)
        print(f"Done. Repacked {len(records)} existing cases.")
        return

    session = make_session()
    if args.ccdi_only:
        print("Loading existing regulatory and Audit Office cases...")
        existing_cases = [record for record in load_existing_cases(ROOT) if record.get("source") != CCDI_SOURCE]
        print("Fetching CCDI/NSC central-level notices...")
        ccdi_cases = [
            enrich_case(record, index)
            for index, record in enumerate(
                parse_ccdi_cases(args.ccdi_limit, args.ccdi_pages, args.ccdi_detail_limit),
                start=1,
            )
        ]
        all_cases = existing_cases + ccdi_cases
        all_cases.sort(key=lambda item: (item.get("publish_date", ""), item["source"], item.get("file_no", "")), reverse=True)
        write_kb(all_cases, ROOT)
        if not args.skip_site:
            write_site(all_cases, ROOT, SITE)
        print(f"Done. Built {len(all_cases)} cases, including {len(ccdi_cases)} CCDI/NSC notices.")
        return

    if args.audit_only:
        print("Loading existing non-Audit Office cases...")
        existing_cases = [record for record in load_existing_cases(ROOT) if record.get("source") != "审计署"]
        print("Fetching Audit Office notices...")
        audit_cases = [enrich_case(record, index) for index, record in enumerate(parse_audit_cases(session, args.audit_limit), start=1)]
        all_cases = existing_cases + audit_cases
        all_cases.sort(key=lambda item: (item.get("publish_date", ""), item["source"], item.get("file_no", "")), reverse=True)
        write_kb(all_cases, ROOT)
        if not args.skip_site:
            write_site(all_cases, ROOT, SITE)
        print(f"Done. Built {len(all_cases)} cases, including {len(audit_cases)} Audit Office cases.")
        return

    print("Discovering MOF cases...")
    mof_records = parse_mof_list(session)
    if args.mof_limit:
        mof_records = mof_records[: args.mof_limit]
    print(f"Fetching {len(mof_records)} MOF cases...")
    mof_cases = []
    for index, record in enumerate(mof_records, start=1):
        try:
            mof_cases.append(parse_mof_case(session, record))
        except Exception as exc:
            print(f"MOF fetch failed: {record['url']} ({exc})")
        if index % 25 == 0:
            print(f"  MOF {index}/{len(mof_records)}")
        time.sleep(0.12)

    print("Fetching CSRC cases...")
    csrc_cases = parse_csrc_list(session, limit=args.csrc_limit)
    print(f"Fetched {len(csrc_cases)} CSRC cases.")

    print("Fetching Audit Office notices...")
    audit_cases = parse_audit_cases(session, limit=args.audit_limit)
    print(f"Fetched {len(audit_cases)} Audit Office cases.")

    print("Fetching CCDI/NSC central-level notices...")
    ccdi_cases = parse_ccdi_cases(args.ccdi_limit, args.ccdi_pages, args.ccdi_detail_limit)
    print(f"Fetched {len(ccdi_cases)} CCDI/NSC notices.")

    all_cases = []
    seen = set()
    print("Fetching MOF hidden-debt notices...")
    try:
        hidden_debt_cases = parse_mof_hidden_debt_cases(session)
    except Exception as exc:
        hidden_debt_cases = []
        print(f"MOF hidden-debt fetch failed: {exc}")

    for case in mof_cases + hidden_debt_cases + csrc_cases + audit_cases + ccdi_cases:
        key = case.get("url") or case.get("sha256")
        if key in seen:
            continue
        seen.add(key)
        all_cases.append(enrich_case(case, len(all_cases) + 1))
    all_cases.sort(key=lambda item: (item.get("publish_date", ""), item["source"], item.get("file_no", "")), reverse=True)

    write_kb(all_cases, ROOT)
    if not args.skip_site:
        write_site(all_cases, ROOT, SITE)
    print(f"Done. Built {len(all_cases)} cases.")


if __name__ == "__main__":
    main()
