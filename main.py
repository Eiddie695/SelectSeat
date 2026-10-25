import json
import time
import argparse
import os
import logging
import datetime

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


from utils import reserve, get_user_credentials

get_current_time = lambda action: (
    time.strftime("%H:%M:%S", time.localtime(time.time() + 8 * 3600))
    if action
    else time.strftime("%H:%M:%S", time.localtime(time.time()))
)
get_current_dayofweek = lambda action: (
    time.strftime("%A", time.localtime(time.time() + 8 * 3600))
    if action
    else time.strftime("%A", time.localtime(time.time()))
)


def wait_for_network(timeout=120):
    """等待网络/DNS就绪，避免电脑被唤醒后立刻连接失败"""
    import socket
    deadline = time.time() + timeout
    host = "passport2.chaoxing.com"
    while time.time() < deadline:
        try:
            socket.getaddrinfo(host, 443)
            logging.info(f"Network ready, resolved {host}")
            return True
        except Exception:
            time.sleep(3)
    logging.error(f"Network not ready after {timeout}s, proceeding anyway")
    return False


SLEEPTIME = 0.05  # 每次抢座的间隔
ENDTIME = "19:40:00"  # 根据学校的预约座位时间+1min即可
RELEASE_TIME = "19:00:00"  # 放号时间：提前预热并卡点提交

ENABLE_SLIDER = True  # 是否有滑块验证
TRY_NO_CAPTCHA = True  # 先尝试不带验证码提交，服务器要求时才自动切到验证码
MAX_ATTEMPT = 5  # 单轮最大尝试次数
RESERVE_NEXT_DAY = True  # 预约明天而不是今天的
RELOGIN_INTERVAL = 300  # 秒，定期重新登录避免 Cookie 过期

ENABLE_MORNING = True  # 是否预约上午时段
ENABLE_NOON = True  # 是否预约中午时段
ENABLE_EVENING = False # 是否预约晚上时段


def wait_until_release(release_time=None):
    """精确等到放号时间，最后 2 秒忙等以保证毫秒级准时"""
    release_time = release_time or RELEASE_TIME
    now = datetime.datetime.now()
    h, m, s = (int(x) for x in release_time.split(":"))
    target = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if target <= now:
        logging.info(f"Release time {release_time} already passed, start immediately")
        return
    delta = (target - now).total_seconds()
    logging.info(f"Waiting {delta:.1f}s until release time {release_time}")
    if delta > 2:
        time.sleep(delta - 2)
    while datetime.datetime.now() < target:
        pass  # 忙等最后 2 秒


def create_session(user, username, password, action):
    """登录一次并预热连接，后续复用同一个会话"""
    s = reserve(
        sleep_time=SLEEPTIME,
        max_attempt=MAX_ATTEMPT,
        enable_slider=ENABLE_SLIDER,
        reserve_next_day=RESERVE_NEXT_DAY,
        try_no_captcha=TRY_NO_CAPTCHA,
    )
    s.get_login_status()
    s.login(username, password)
    s.requests.headers.update({"Host": "office.chaoxing.com"})
    s.page_seat = user.get("pageid")
    s.prewarm(user.get("roomid"), user.get("seatid"), action)
    return s


def login_and_reserve(users, usernames, passwords, action, success_list=None, sessions=None):
    logging.info(
        f"Global settings: \nSLEEPTIME: {SLEEPTIME}\nENDTIME: {ENDTIME}\nENABLE_SLIDER: {ENABLE_SLIDER}\nRESERVE_NEXT_DAY: {RESERVE_NEXT_DAY}\nENABLE_MORNING: {ENABLE_MORNING}\nENABLE_NOON: {ENABLE_NOON}\nENABLE_EVENING: {ENABLE_EVENING}"
    )
    if action and len(usernames.split(",")) != len(users):
        raise Exception("user number should match the number of config")
    if success_list is None:
        success_list = [[False, False, False] for _ in users]
    current_dayofweek = get_current_dayofweek(action)
    time_slots = [
        ("morning", ENABLE_MORNING, "time_morning"),
        ("noon", ENABLE_NOON, "time_noon"),
        ("evening", ENABLE_EVENING, "time_evening"),
    ]
    for index, user in enumerate(users):
        username = user.get("username")
        password = user.get("password")
        roomid = user.get("roomid")
        seatid = user.get("seatid")
        daysofweek = user.get("daysofweek")
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        if current_dayofweek not in daysofweek:
            logging.info("Today not set to reserve")
            continue
        if sessions and index in sessions:
            s = sessions[index]
            if time.time() - getattr(s, "last_login_ts", 0) > RELOGIN_INTERVAL:
                s.login(username, password)
        else:
            s = create_session(user, username, password, action)
        for slot_name, enabled, config_key in time_slots:
            if not enabled:
                logging.info(f"Skip {slot_name} slot (disabled)")
                continue
            times = user.get(config_key)
            if not times:
                logging.info(f"Skip {slot_name} slot (no time configured)")
                continue
            slot_index = time_slots.index((slot_name, enabled, config_key))
            if success_list[index][slot_index]:
                logging.info(f"Skip {slot_name} slot (already reserved)")
                continue
            s.max_attempt = MAX_ATTEMPT
            logging.info(
                f"----------- {username} -- {slot_name} -- {times} -- {seatid} try -----------"
            )
            suc = s.submit(times, roomid, seatid, action)
            success_list[index][slot_index] = suc
    return success_list


def main(users, action=False):
    wait_for_network()  # 电脑被唤醒后等网络就绪
    current_time = get_current_time(action)
    logging.info(f"start time {current_time}, action {'on' if action else 'off'}")
    attempt_times = 0
    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
    success_list = None
    current_dayofweek = get_current_dayofweek(action)
    enabled_slots = sum([ENABLE_MORNING, ENABLE_NOON, ENABLE_EVENING])
    today_reservation_num = sum(
        enabled_slots for d in users if current_dayofweek in d.get("daysofweek")
    )
    # 提前登录并预热连接，只做一次；后续循环复用同一会话
    sessions = {}
    for index, user in enumerate(users):
        if current_dayofweek not in user.get("daysofweek"):
            continue
        username, password = user.get("username"), user.get("password")
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        sessions[index] = create_session(user, username, password, action)
    # 预热完成后精确等到放号时间再开始抢
    wait_until_release()
    current_time = get_current_time(action)
    while current_time < ENDTIME:
        attempt_times += 1
        # try:
        success_list = login_and_reserve(
            users, usernames, passwords, action, success_list, sessions=sessions
        )
        # except Exception as e:
        #     print(f"An error occurred: {e}")
        total_success = sum(sum(slot) for slot in success_list) if success_list else 0
        print(
            f"attempt time {attempt_times}, time now {current_time}, success list {success_list}"
        )
        current_time = get_current_time(action)
        if total_success == today_reservation_num:
            print(f"reserved successfully!")
            return


def debug(users, action=False):
    logging.info(
        f"Global settings: \nSLEEPTIME: {SLEEPTIME}\nENDTIME: {ENDTIME}\nENABLE_SLIDER: {ENABLE_SLIDER}\nRESERVE_NEXT_DAY: {RESERVE_NEXT_DAY}\nENABLE_MORNING: {ENABLE_MORNING}\nENABLE_NOON: {ENABLE_NOON}\nENABLE_EVENING: {ENABLE_EVENING}"
    )
    suc = False
    logging.info(f" Debug Mode start! , action {'on' if action else 'off'}")
    if action:
        usernames, passwords = get_user_credentials(action)
    current_dayofweek = get_current_dayofweek(action)
    time_slots = [
        ("morning", ENABLE_MORNING, "time_morning"),
        ("noon", ENABLE_NOON, "time_noon"),
        ("evening", ENABLE_EVENING, "time_evening"),
    ]
    for index, user in enumerate(users):
        username = user.get("username")
        password = user.get("password")
        roomid = user.get("roomid")
        seatid = user.get("seatid")
        daysofweek = user.get("daysofweek")
        if type(seatid) == str:
            seatid = [seatid]
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        if current_dayofweek not in daysofweek:
            logging.info("Today not set to reserve")
            continue
        s = reserve(
            sleep_time=SLEEPTIME,
            max_attempt=MAX_ATTEMPT,
            enable_slider=ENABLE_SLIDER,
            reserve_next_day=RESERVE_NEXT_DAY,
        )
        s.get_login_status()
        s.login(username, password)
        s.requests.headers.update({"Host": "office.chaoxing.com"})
        s.page_seat = user.get("pageid")  # 内部记录ID(105)用于取页面token，真实座位号(212)用于提交
        for slot_name, enabled, config_key in time_slots:
            if not enabled:
                logging.info(f"Skip {slot_name} slot (disabled)")
                continue
            times = user.get(config_key)
            if not times:
                logging.info(f"Skip {slot_name} slot (no time configured)")
                continue
            logging.info(f"----------- {username} -- {slot_name} -- {times} -- {seatid} try -----------")
            s.max_attempt = MAX_ATTEMPT  # 重置尝试次数，避免被上一个时段耗尽
            suc = s.submit(times, roomid, seatid, action)
            if suc:
                return


def get_roomid(args1, args2):
    username = input("请输入用户名：")
    password = input("请输入密码：")
    s = reserve(
        sleep_time=SLEEPTIME,
        max_attempt=MAX_ATTEMPT,
        enable_slider=ENABLE_SLIDER,
        reserve_next_day=RESERVE_NEXT_DAY,
    )
    s.get_login_status()
    s.login(username=username, password=password)
    s.requests.headers.update({"Host": "office.chaoxing.com"})
    encode = input("请输入deptldEnc：")
    s.roomid(encode)


if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    parser = argparse.ArgumentParser(prog="Chao Xing seat auto reserve")
    parser.add_argument("-u", "--user", default=config_path, help="user config file")
    parser.add_argument(
        "-m",
        "--method",
        default="reserve",
        choices=["reserve", "debug", "room"],
        help="for debug",
    )
    parser.add_argument(
        "-a",
        "--action",
        action="store_true",
        help="use --action to enable in github action",
    )
    args = parser.parse_args()
    func_dict = {"reserve": main, "debug": debug, "room": get_roomid}
    with open(args.user, "r+") as data:
        usersdata = json.load(data)["reserve"]
    func_dict[args.method](usersdata, args.action)