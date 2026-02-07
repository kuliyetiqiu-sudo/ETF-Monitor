import requests
import re
import json
import time
import sys
from datetime import datetime
import pytz
import urllib3
import traceback 

# 强制设置输出编码为 utf-8，防止日志乱码
sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

# ==============================================================================
# 🎯 V16.0 GitHub 专享版：完美适配 requirements.txt + 修复Bug
# ==============================================================================

# 🔴🔴🔴 你的 PushPlus Token 🔴🔴🔴
PUSHPLUS_TOKEN = '229e6e58116042c8a0065709dd98eabc' 

# 核心策略阈值
THRESHOLDS = {
    "ATTACK": 1.0,           # 进攻：价差 < 1.0%
    "RETREAT": 3.0,          # 撤退：价差 > 3.0%
    "MAX_ABS_PREMIUM": 6.5   # 风控：绝对溢价 > 6.5%
}

# 1对2 监控配置
GROUPS = [
    {
        "name": "纳指组",
        "base": {"code": "159659", "name": "招商纳指", "symbol": "sz159659", "index": "gb_ndx", "future": "NQ"},
        "targets": [
            {"code": "513100", "name": "国泰沪", "symbol": "sh513100", "index": "gb_ndx", "future": "NQ"},
            {"code": "159501", "name": "嘉实纳指", "symbol": "sz159501", "index": "gb_ndx", "future": "NQ"}
        ]
    },
    {
        "name": "标普组",
        "base": {"code": "159655", "name": "华夏标普", "symbol": "sz159655", "index": "gb_inx", "future": "ES"},
        "targets": [
            {"code": "513500", "name": "博时标普", "symbol": "sh513500", "index": "gb_inx", "future": "ES"},
            {"code": "159612", "name": "国泰标普", "symbol": "sz159612", "index": "gb_inx", "future": "ES"}
        ]
    }
]

# 全局变量
last_alert_time = {}
alert_counts = {}
dca_daily_sent = False 

def send_wechat(title, content):
    """推送通知"""
    url = 'http://www.pushplus.plus/send'
    data = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "html"}
    try: requests.post(url, json=data, timeout=5)
    except: pass

def get_market_factors():
    """获取行情因子"""
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        url = "http://hq.sinajs.cn/list=gb_ndx,gb_inx,hf_NQ,hf_ES,fx_susdcnh"
        resp = requests.get(url, headers=headers, timeout=5)
        data = {}
        for line in resp.text.strip().split('\n'):
            if "=" in line:
                key = line.split('=')[0].split('_')[-1]
                val = line.split('=')[1].strip('";').split(',')
                data[key] = val
        return {
            'ndx_close': float(data['ndx'][2]) / 100,
            'inx_close': float(data['inx'][2]) / 100,
            'nq_future': (float(data['NQ'][0]) - float(data['NQ'][7])) / float(data['NQ'][7]),
            'es_future': (float(data['ES'][0]) - float(data['ES'][7])) / float(data['ES'][7]),
            'usd_cnh': (float(data['susdcnh'][1]) - float(data['susdcnh'][3])) / float(data['susdcnh'][3])
        }
    except: return None

def calc_premium(conf, factors):
    """计算真溢价率"""
    try:
        # 1. 查现价
        r_p = requests.get(f"http://qt.gtimg.cn/q={conf['symbol']}", timeout=2)
        p_vals = r_p.content.decode('gbk', errors='ignore').split('~')
        price = float(p_vals[3]) if float(p_vals[3]) > 0 else float(p_vals[4])
        
        # 2. 查净值
        ts = int(time.time() * 1000)
        r_n = requests.get(f"http://fundgz.1234567.com.cn/js/{conf['code']}.js?rt={ts}", timeout=2)
        match = re.search(r'jsonpgz\((.*?)\);', r_n.text)
        if not match: return None
        nav = float(json.loads(match.group(1))['dwjz'])

        # 3. 计算IOPV
        close_pct = factors['inx_close'] if conf['index'] == 'gb_inx' else factors['ndx_close']
        future_pct = factors['es_future'] if conf['future'] == 'ES' else factors['nq_future']
        iopv = nav * (1 + close_pct) * (1 + future_pct) * (1 + factors['usd_cnh'])
        
        return (price - iopv) / iopv * 100
    except: return None

def get_dca_advice(code, premium_real, day):
    """🧠 定投决策模块 (区分日期)"""
    # 15号-月底是严选期，1号-14号是扫尾期
    if day >= 15: period_name, is_strict = "上半月·严选期", True
    else: period_name, is_strict = "下半月·扫尾期", False

    # 招商纳指 (159659)
    if code == "159659":
        if premium_real < 0.2: return f"🟢 钻石底 ({period_name})", "梭哈本月额度 (4份)"
        if premium_real > 1.3: return f"🔴 太贵了 ({period_name})", "停手 (0份)"
        if is_strict:
            return (f"🟡 舒适区 ({period_name})", "买入 1 份") if premium_real < 0.6 else (f"🟠 略高 ({period_name})", "观望")
        else:
            return (f"🟡 追赶区 ({period_name})", "买入 2 份") if premium_real < 1.0 else (f"🟠 勉强 ({period_name})", "买入 1 份")

    # 华夏标普 (159655)
    elif code == "159655":
        if premium_real < -0.8: return f"🟢 黄金坑 ({period_name})", "梭哈本月额度 (2份)"
        if premium_real > 0.8: return f"🔴 太贵了 ({period_name})", "停手 (0份)"
        if is_strict:
            return (f"🟡 舒适区 ({period_name})", "买入 1 份") if premium_real < 0.0 else (f"🟠 不折价 ({period_name})", "观望")
        else:
            return (f"🟡 扫尾区 ({period_name})", "买完剩余") if premium_real < 0.5 else (f"🟠 略高 ({period_name})", "少量补仓")
            
    return None, None

def monitor_logic(now_time):
    # 【修复关键】声明全局变量，否则会报错 UnboundLocalError
    global dca_daily_sent 
    
    f = get_market_factors()
    if not f: return
    
    # === A. 定投日报模块 (每天14:45触发) ===
    current_hhmm = now_time.hour * 100 + now_time.minute
    # 如果时间在 14:45 - 14:55 之间，且今天还没发过
    if 1445 <= current_hhmm <= 1455 and not dca_daily_sent:
        print("📅 生成定投日报...")
        dca_msg = "<h3>📅 今日定投操作指南 (14:45)</h3>"
        
        # 获取招商纳指
        p_159659 = calc_premium({"code":"159659","symbol":"sz159659","index":"gb_ndx","future":"NQ"}, f)
        if p_159659 is not None:
            status, action = get_dca_advice("159659", p_159659, now_time.day)
            dca_msg += f"<p><b>🏠 招商纳指 (159659)</b><br>真溢价: {p_159659:.2f}%<br>评价: {status}<br>👉 <b>指令: {action}</b></p>"
            
        # 获取华夏标普
        p_159655 = calc_premium({"code":"159655","symbol":"sz159655","index":"gb_inx","future":"ES"}, f)
        if p_159655 is not None:
            status, action = get_dca_advice("159655", p_159655, now_time.day)
            dca_msg += f"<p><b>🏠 华夏标普 (159655)</b><br>真溢价: {p_159655:.2f}%<br>评价: {status}<br>👉 <b>指令: {action}</b></p>"
            
        send_wechat("📅 定投日报: 该下单了", dca_msg)
        dca_daily_sent = True # 标记为已发送，防止重复发
        print("✅ 定投日报已发送")

    # === B. 套利轮动监控模块 (全天运行，无视日期) ===
    print(f"[{now_time.strftime('%H:%M:%S')}] 监控中... NQ:{f['nq_future']*100:+.2f}%")

    for group in GROUPS:
        p_base = calc_premium(group['base'], f)
        if p_base is None: continue

        for target in group['targets']:
            p_target = calc_premium(target, f)
            if p_target is None: continue

            # 纯价差计算
            spread = p_target - p_base
            alert_title, alert_msg = None, None

            # 进攻信号
            if spread < THRESHOLDS['ATTACK']:
                if p_target < THRESHOLDS['MAX_ABS_PREMIUM']:
                    alert_title = f"⚔️ 进攻机会: {target['name']}"
                    alert_msg = f"策略: 卖出 {group['base']['name']} -> 买入 {target['name']}<br>价差: <font color='green'>{spread:.2f}%</font>"
            # 撤退信号
            elif spread > THRESHOLDS['RETREAT']:
                alert_title = f"🔥 撤退信号: {target['name']}"
                alert_msg = f"策略: 止盈 {target['name']} -> 回防 {group['base']['name']}<br>价差: <font color='red'>{spread:.2f}%</font>"

            if alert_title:
                key = f"{target['code']}_{alert_title}"
                current_count = alert_counts.get(key, 0)
                cooldown = 600 if current_count < 3 else 3600
                # 冷却时间判断
                if key not in last_alert_time or (time.time() - last_alert_time[key] > cooldown):
                    print(f"🚀 发送报警: {alert_title}")
                    send_wechat(alert_title, alert_msg)
                    last_alert_time[key] = time.time()
                    alert_counts[key] = current_count + 1

if __name__ == "__main__":
    try:
        # 使用 pytz 获取上海时间，完美适配你的 requirements.txt
        tz = pytz.timezone('Asia/Shanghai')
        print(f"🚀 云端监控 V16.0 (GitHub 专享修复版) 启动...")
        
        start_time = time.time()
        # 5小时55分 (21300秒) 后自动退出，防止 GitHub 强制杀后台
        MAX_RUN_TIME = 21300 

        while True:
            # 0. 自动下班机制
            if time.time() - start_time > MAX_RUN_TIME: 
                print("👋 运行时间达标，主动下班。")
                break
            
            now = datetime.now(tz)
            
            # 1. 周末休息
            if now.weekday() > 4: 
                print(f"😴 周末休息... {now.strftime('%m-%d %H:%M')}")
                time.sleep(300); continue
            
            # 2. 交易时间判断 (09:15 - 15:15)
            # 配合你的 Cron 09:10 启动，正好能覆盖全天
            current_time = now.hour * 100 + now.minute
            if current_time < 915:
                print(f"⏳ 等待开盘... {now.strftime('%H:%M')}")
                time.sleep(300); continue
            if current_time > 1515: 
                print(f"🌙 已收盘... {now.strftime('%H:%M')}")
                # 既然一天只跑一次，收盘后就可以直接结束了
                break 

            try: monitor_logic(now)
            except: 
                print("⚠️ 轮询出错:", traceback.format_exc())
            
            time.sleep(60)

    except Exception as e:
        print(traceback.format_exc())