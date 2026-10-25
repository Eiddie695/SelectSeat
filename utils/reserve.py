from utils import AES_Encrypt, enc, generate_captcha_key, verify_param
import json
import requests
import re
import time
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib3.exceptions import InsecureRequestWarning


def get_date(day_offset: int = 0):
    today = datetime.datetime.now().date()
    offset_day = today + datetime.timedelta(days=day_offset)
    tomorrow = offset_day.strftime("%Y-%m-%d")
    return tomorrow


class reserve:
    def __init__(
        self,
        sleep_time=0.2,
        max_attempt=50,
        enable_slider=False,
        reserve_next_day=False,
        try_no_captcha=False,
    ):
        self.login_page = (
            "https://passport2.chaoxing.com/mlogin?loginType=1&newversion=true&fid="
        )
        self.url = (
            "https://office.chaoxing.com/front/third/apps/seatengine/select?id={}&day={}&backLevel=2&seatId={}&fidEnc=0fd2b43990df8985&wfwEngineEnc="
        )
        self.submit_url = "https://office.chaoxing.com/data/apps/seatengine/submit"
        self.seat_url = "https://office.chaoxing.com/data/apps/seat/getusedtimes"
        self.login_url = "https://passport2.chaoxing.com/fanyalogin"
        self.token = ""
        self.success_times = 0
        self.fail_dict = []
        self.submit_msg = []
        self.requests = requests.session()
        self.token_pattern = re.compile("token = '(.*?)'")
        self.headers = {
            "Referer": "https://office.chaoxing.com/",
            "Host": "captcha.chaoxing.com",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        self.login_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "accept-encoding": "gzip, deflate, br, zstd",
            "cache-control": "no-cache",
            "Connection": "keep-alive",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 10_3_1 like Mac OS X) AppleWebKit/603.1.3 (KHTML, like Gecko) Version/10.0 Mobile/14E304 Safari/602.1 wechatdevtools/1.05.2109131 MicroMessenger/8.0.5 Language/zh_CN webview/16364215743155638",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Host": "passport2.chaoxing.com",
        }

        self.sleep_time = sleep_time
        self.max_attempt = max_attempt
        self.enable_slider = enable_slider
        self.reserve_next_day = reserve_next_day
        self.try_no_captcha = try_no_captcha
        # 首次先尝试不带验证码提交；若服务器要求验证码，会自动切到验证码模式
        self._need_captcha = bool(enable_slider) and not try_no_captcha
        self.last_msg = ""
        self.last_login_ts = 0
        # 验证码使用独立会话，避免与主页token请求并发时相互干扰
        self.captcha_requests = requests.session()
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    # login and page token
    def _get_page_token(self, url, require_value=False):
        response = self.requests.get(url=url, verify=False)
        html = response.content.decode("utf-8")
        matches = re.findall(r'id="submit_enc"\s+value="(.*?)"', html)
        token_matches = re.findall(r'token\s*=\s*[\'"](.*?)[\'"]', html)
        if not matches and token_matches:
            matches = token_matches
        value_matches = None
        if require_value:
            value_matches = re.findall(r'name="submit_enc"\s+value="(.*?)"', html)
            if not value_matches:
                value_matches = re.findall(r'value="([a-f0-9_]+)"', html)
        if not matches:
            logging.error(f"Failed to get token from {url}")
            logging.info(f"First 2000 chars of HTML: {html[:2000]}")
            return "", ""
        return matches[0] if matches else "", value_matches[0] if value_matches else ""

    def get_login_status(self):
        self.requests.headers = self.login_headers
        self.requests.get(url=self.login_page, verify=False)

    def login(self, username, password):
        username = AES_Encrypt(username)
        password = AES_Encrypt(password)
        parm = {
            "fid": -1,
            "uname": username,
            "password": password,
            "refer": "http%3A%2F%2Foffice.chaoxing.com%2Ffront%2Fthird%2Fapps%2Fseat%2Fcode%3Fid%3D4219%26seatNum%3D380",
            "t": True,
        }
        self.requests.headers["Host"] = "passport2.chaoxing.com"
        jsons = self.requests.post(url=self.login_url, params=parm, verify=False)
        self.requests.headers["Host"] = "office.chaoxing.com"
        obj = jsons.json()
        if obj["status"]:
            self.last_login_ts = time.time()
            logging.info(f"User {username} login successfully")
            return (True, "")
        else:
            logging.info(
                f"User {username} login failed. Please check you password and username! "
            )
            return (False, obj["msg2"])

    # extra: get roomid
    def roomid(self, encode):
        url = f"https://office.chaoxing.com/data/apps/seat/room/list?cpage=1&pageSize=100&firstLevelName=&secondLevelName=&thirdLevelName=&deptIdEnc={encode}"
        json_data = self.requests.get(url=url).content.decode("utf-8")
        ori_data = json.loads(json_data)
        for i in ori_data["data"]["seatRoomList"]:
            info = f'{i["firstLevelName"]}-{i["secondLevelName"]}-{i["thirdLevelName"]} id为：{i["id"]}'
            print(info)

    # 提前预热：完成 DNS/TLS 握手并跑一次完整的 token+验证码流程（结果丢弃）
    # 目的：把 2~3 秒的冷启动开销提前到放号时间之前
    def prewarm(self, roomid, seatid, action=False):
        delta_day = 1 if self.reserve_next_day else 0
        day = datetime.date.today() + datetime.timedelta(days=delta_day)
        if action:
            day = datetime.date.today() + datetime.timedelta(days=1 + delta_day)
        seat = seatid[0] if isinstance(seatid, (list, tuple)) else seatid
        page_seat = getattr(self, "page_seat", None) or seat
        url = self.url.format(roomid, day, page_seat)
        start = time.time()
        try:
            self._get_page_token(url, require_value=True)
            if self.enable_slider:
                self.resolve_captcha()
        except Exception as e:
            logging.info(f"Prewarm warning: {e}")
        logging.info(f"Prewarm done in {time.time() - start:.2f}s")

    @staticmethod
    def _is_captcha_error(msg):
        if not msg:
            return False
        keys = ("验证码", "安全验证", "代码:302", "代码:303", "代码:301")
        return any(k in msg for k in keys)

    def _fetch_token_and_captcha(self, url):
        """token 与验证码互不依赖，用两个线程并发获取"""
        with ThreadPoolExecutor(max_workers=2) as pool:
            token_future = pool.submit(self._get_page_token, url, True)
            captcha_future = pool.submit(self.resolve_captcha)
            token, value = token_future.result()
            captcha = captcha_future.result()
        return token, value, captcha

    # solve captcha

    def resolve_captcha(self):
        logging.info(f"Start to resolve captcha token")
        # 复用主会话的 cookie，但使用独立连接池以支持并发
        self.captcha_requests.cookies.update(self.requests.cookies)
        captcha_token, bg, tp = self.get_slide_captcha_data()
        logging.info(f"Successfully get prepared captcha_token {captcha_token}")
        logging.info(f"Captcha Image URL-small {tp}, URL-big {bg}")
        x = self.x_distance(bg, tp)
        logging.info(f"Successfully calculate the captcha distance {x}")

        params = {
            "callback": "jQuery33109180509737430778_1716381333117",
            "captchaId": "42sxgHoTPTKbt0uZxPJ7ssOvtXr3ZgZ1",
            "type": "slide",
            "token": captcha_token,
            "textClickArr": json.dumps([{"x": x}]),
            "coordinate": json.dumps([]),
            "runEnv": "10",
            "version": "1.1.18",
            "_": int(time.time() * 1000),
        }
        response = self.captcha_requests.get(
            f"https://captcha.chaoxing.com/captcha/check/verification/result",
            params=params,
            headers=self.headers,
            verify=False,
        )
        text = response.text.replace(
            "jQuery33109180509737430778_1716381333117(", ""
        ).replace(")", "")
        data = json.loads(text)
        logging.info(f"Successfully resolve the captcha token {data}")
        try:
            validate_val = json.loads(data["extraData"])["validate"]
            return validate_val
        except KeyError as e:
            logging.info("Can't load validate value. Maybe server return mistake.")
            return ""

    def get_slide_captcha_data(self):
        url = "https://captcha.chaoxing.com/captcha/get/verification/image"
        timestamp = int(time.time() * 1000)
        capture_key, token = generate_captcha_key(timestamp)
        referer = f"https://office.chaoxing.com/front/third/apps/seat/code?id=3993&seatNum=0199"
        params = {
            "callback": f"jQuery33107685004390294206_1716461324846",
            "captchaId": "42sxgHoTPTKbt0uZxPJ7ssOvtXr3ZgZ1",
            "type": "slide",
            "version": "1.1.18",
            "captchaKey": capture_key,
            "token": token,
            "referer": referer,
            "_": timestamp,
            "d": "a",
            "b": "a",
        }
        response = self.captcha_requests.get(
            url=url, params=params, headers=self.headers, verify=False
        )
        content = response.text

        data = content.replace(
            "jQuery33107685004390294206_1716461324846(", ")"
        ).replace(")", "")
        data = json.loads(data)
        captcha_token = data["token"]
        bg = data["imageVerificationVo"]["shadeImage"]
        tp = data["imageVerificationVo"]["cutoutImage"]
        return captcha_token, bg, tp

    def x_distance(self, bg, tp):
        import numpy as np
        import cv2

        def cut_slide(slide):
            slider_array = np.frombuffer(slide, np.uint8)
            slider_image = cv2.imdecode(slider_array, cv2.IMREAD_UNCHANGED)
            slider_part = slider_image[:, :, :3]
            mask = slider_image[:, :, 3]
            mask[mask != 0] = 255
            x, y, w, h = cv2.boundingRect(mask)
            cropped_image = slider_part[y : y + h, x : x + w]
            return cropped_image

        c_captcha_headers = {
            "Referer": "https://office.chaoxing.com/",
            "Host": "captcha-b.chaoxing.com",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        # 两张验证码图片并发下载
        with ThreadPoolExecutor(max_workers=2) as pool:
            bg_future = pool.submit(
                self.captcha_requests.get, bg, headers=c_captcha_headers, verify=False
            )
            tp_future = pool.submit(
                self.captcha_requests.get, tp, headers=c_captcha_headers, verify=False
            )
            bg, tp = bg_future.result().content, tp_future.result().content
        bg_img = cv2.imdecode(np.frombuffer(bg, np.uint8), cv2.IMREAD_COLOR)
        tp_img = cut_slide(tp)
        bg_edge = cv2.Canny(bg_img, 100, 200)
        tp_edge = cv2.Canny(tp_img, 100, 200)
        bg_pic = cv2.cvtColor(bg_edge, cv2.COLOR_GRAY2RGB)
        tp_pic = cv2.cvtColor(tp_edge, cv2.COLOR_GRAY2RGB)
        res = cv2.matchTemplate(bg_pic, tp_pic, cv2.TM_CCOEFF_NORMED)
        _, _, _, max_loc = cv2.minMaxLoc(res)
        tl = max_loc
        return tl[0]

    def submit(self, times, roomid, seatid, action):
        delta_day = 1 if self.reserve_next_day else 0
        day = datetime.date.today() + datetime.timedelta(days=0 + delta_day)
        if action:
            day = datetime.date.today() + datetime.timedelta(days=1 + delta_day)
        seats = [seatid] if isinstance(seatid, str) else list(seatid)
        suc = False
        for seat in seats:
            page_seat = getattr(self, "page_seat", None) or seat
            url = self.url.format(roomid, day, page_seat)
            while not suc and self.max_attempt > 0:
                self.max_attempt -= 1
                if self._need_captcha and self.enable_slider:
                    # token 与验证码并发获取，节省约 0.2s
                    token, value, captcha = self._fetch_token_and_captcha(url)
                else:
                    token, value = self._get_page_token(url, require_value=True)
                    captcha = ""
                logging.info(f"Get token: {token}")
                if captcha:
                    logging.info(f"Captcha token {captcha}")
                suc = self.get_submit(
                    self.submit_url,
                    times=times,
                    token=token,
                    roomid=roomid,
                    seatid=seat,
                    captcha=captcha,
                    action=action,
                    value=value,
                )
                if suc:
                    return suc
                if self._is_captcha_error(self.last_msg):
                    # 服务器要求验证码或验证码失效：立即切换并重试
                    logging.info(
                        f"Captcha required ({self.last_msg}), switching to captcha mode"
                    )
                    self._need_captcha = True
                    continue
                time.sleep(self.sleep_time)
        return suc

    def get_submit(
        self, url, times, token, roomid, seatid, captcha="", action=False, value=""
    ):
        delta_day = 1 if self.reserve_next_day else 0
        day = datetime.date.today() + datetime.timedelta(
            days=0 + delta_day
        )  # 预约今天，修改days=1表示预约明天
        if action:
            day = datetime.date.today() + datetime.timedelta(
                days=1 + delta_day
            )  # 由于action时区问题导致其早+8区一天
        parm = {
            "roomId": roomid,
            "startTime": times[0],
            "endTime": times[1],
            "day": str(day),
            "seatNum": seatid,
            "captcha": captcha,
            "token": token,
            "type": "1",
            "verifyData": "1",
        }
        logging.info(f"submit parameter {parm} ")
        # parm["enc"] = enc(parm)
        parm["enc"] = verify_param(parm, value)
        html = self.requests.post(url=url, params=parm, verify=False).content.decode(
            "utf-8"
        )
        try:
            data = json.loads(html)
        except json.JSONDecodeError:
            # 服务器可能返回 302 验证页或 HTML 错误页
            self.last_msg = "non-json response"
            logging.info(f"submit non-json response: {html[:200]}")
            return False
        self.last_msg = data.get("msg", "")
        self.submit_msg.append(
            times[0] + "~" + times[1] + ":  " + str(data)
        )
        logging.info(data)
        return data["success"]