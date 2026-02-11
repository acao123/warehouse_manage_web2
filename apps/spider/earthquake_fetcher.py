import requests
import random
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class EarthquakeInfo:
    """地震信息封装对象"""
    ori_time: datetime      # 发震时间（日期类型）
    loc_name: str           # 震中位置
    epi_lon: float          # 震中经度（四舍五入保留2位小数）
    epi_lat: float          # 震中纬度（四舍五入保留2位小数）
    foc_depth: int          # 震源深度
    magnitude: float        # 震级

    def __str__(self) -> str:
        return (
            f"发震时间: {self.ori_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"震中位置: {self.loc_name}\n"
            f"震中经度: {self.epi_lon}\n"
            f"震中纬度: {self.epi_lat}\n"
            f"震源深度: {self.foc_depth} km\n"
            f"震级:     M{self.magnitude}"
        )


class EarthquakeFetcher:
    """中国地震台网 地震目录数据获取类（反反爬版）"""

    BASE_URL = "https://www.cenc.ac.cn/prodlaunch-web-backend/open/data/catalogs"

    # 固定参数
    FIXED_PARAMS = {
        "orderBy": "id",
        "isAsc": "false",
        "startMg": 3,
        "endMg": 10,
        "locationRange": 1,
    }

    # ==================== User-Agent 池 ====================
    USER_AGENTS = [
        # Chrome (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Chrome (Mac)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # Firefox (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        # Firefox (Mac)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
        # Edge (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        # Safari (Mac)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        # Chrome (Linux)
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # Firefox (Linux)
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    ]

    # ==================== Referer 池 ====================
    REFERERS = [
        "https://www.cenc.ac.cn/",
        "https://www.cenc.ac.cn/cenc/dzxx/index.html",
        "https://news.ceic.ac.cn/",
    ]

    # ==================== Accept-Language 池 ====================
    ACCEPT_LANGUAGES = [
        "zh-CN,zh;q=0.9,en;q=0.8",
        "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "zh-CN,zh;q=0.9",
        "zh-CN,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
        "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    ]

    def _generate_random_ip(self) -> str:
        """生成随机的国内公网 IP 地址"""
        # 常见国内 IP 段前缀
        ip_prefixes = [
            (36, 56),   (58, 62),   (101, 101), (103, 103),
            (106, 106), (110, 112), (113, 120), (121, 125),
            (171, 171), (175, 175), (180, 183), (210, 215),
            (218, 222),
        ]
        prefix = random.choice(ip_prefixes)
        first = random.randint(prefix[0], prefix[1])
        return f"{first}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

    def _build_headers(self) -> dict:
        """每次请求时动态生成随机请求头"""
        fake_ip = self._generate_random_ip()
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Referer": random.choice(self.REFERERS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": random.choice(self.ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": random.choice(["no-cache", "max-age=0"]),
            # 模拟代理 IP —— 服务端可能识别这些头来获取"客户端 IP"
            "X-Forwarded-For": fake_ip,
            "X-Real-IP": fake_ip,
            "X-Client-IP": fake_ip,
        }

    def _build_params(self) -> dict:
        """构建请求参数，动态计算 startTime 和 endTime"""
        now = datetime.now()

        # endTime: 当天 23:59:59
        end_time = now.replace(hour=23, minute=59, second=59, microsecond=0)

        # startTime: 当前时间的第二天凌晨 - 6天
        start_time = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=6)

        params = {
            **self.FIXED_PARAMS,
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return params

    def fetch_first(self) -> EarthquakeInfo:
        """
        请求接口并返回第一条地震数据的封装对象。
        如果接口无数据则抛出异常。
        """
        params = self._build_params()
        headers = self._build_headers()

        print(f"[DEBUG] User-Agent : {headers['User-Agent'][:60]}...")
        print(f"[DEBUG] Fake IP    : {headers['X-Forwarded-For']}")
        print(f"[DEBUG] Referer    : {headers['Referer']}")
        print(f"[DEBUG] startTime  : {params['startTime']}")
        print(f"[DEBUG] endTime    : {params['endTime']}")
        print("-" * 60)

        response = requests.get(
            self.BASE_URL,
            params=params,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()

        result = response.json()

        if result.get("code") != 0:
            raise RuntimeError(
                f"接口返回错误, code={result.get('code')}, "
                f"message={result.get('message')}"
            )

        data_list = result.get("data")
        if not data_list:
            raise ValueError("接口返回数据为空，没有符合条件的地震记录")

        first = data_list[0]

        earthquake = EarthquakeInfo(
            ori_time=datetime.strptime(first["oriTime"], "%Y-%m-%d %H:%M:%S"),
            loc_name=first["locName"].strip(),
            epi_lon=round(float(first["epiLon"]), 2),
            epi_lat=round(float(first["epiLat"]), 2),
            foc_depth=int(first["focDepth"]),
            magnitude=float(first["magnitude"]),
        )

        return earthquake


# ===================== 使用示例 =====================
if __name__ == "__main__":
    fetcher = EarthquakeFetcher()
    info = fetcher.fetch_first()
    print(info)