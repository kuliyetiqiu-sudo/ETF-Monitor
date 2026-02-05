import requests
import re
import json
import time
from datetime import datetime
import pytz
import urllib3
import traceback # 引入错误追踪，防止闪退看不到报错

urllib3.disable_warnings()

# ==============================================================================
# 🎯 V11.8 永不掉线版：1守2攻 + 24小时待机
# ==============================================================================

# 🔴🔴🔴 请确认你的 PushPlus Token 🔴🔴🔴
PUSHPLUS_TOKEN = '229e6e58116042c8a0065709dd98eabc' 

# 核心策略阈值
THRESHOLDS = {
    "ATTACK": 1.0,           # 进攻：价差 < 1.0%
    "RETREAT": 3.0,          # 撤退：价差 > 3.0%
    "MAX_ABS_PREMIUM": 6.5   # 风控：绝对溢价 > 6.5% 禁止买入
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

# 全局变量记录报警状态
last_alert_time = {}
alert_counts = {}

def send_wechat(title, content):
    """发送微信通知"""
    url = 'http://www.pushplus.plus/send'
    data = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "html"}
    try: 
        requests.post(url, json=data, timeout=5)
    except: 
        pass

def get_market_factors():
    """获取期货和汇率因子"""
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
    """计算单个ETF的真实溢价率"""
    try:
        # 1. 查现价
        r_p = requests.get(f"http://qt.gtimg.cn/q={conf['symbol']}", timeout=2)
        p_vals = r_p.content.decode('gbk', errors='ignore').split('~')
        price = float(p_vals[3]) if float(p_vals[3]) > 0 else float(p_vals[4])
        
        # 2. 查T-1净值
        ts = int(time.time() * 1000)
        r_n = requests.get(f"http://fundgz.1234567.com.cn/js/{conf['code']}.js?rt={ts}", timeout=2)
        match = re.search(r'jsonpgz\((.*?)\);', r_n.text)
        if not match: return None
        nav = float(json.loads(match.group(1))['dwjz'])

        # 3. 计算实时IOPV
        close_pct = factors['inx_close'] if conf['index'] == 'gb_inx' else factors['ndx_close']
        future_pct = factors['es_future'] if conf['future'] == 'ES' else factors['nq_future']
        
        iopv = nav * (1 + close_pct) * (1 + future_pct) * (1 + factors['usd_cnh'])
        
        return (price - iopv) / iopv * 100
    except: return None

def monitor_logic():
    """核心监控逻辑"""
    f = get_market_factors()
    if not f: return
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] NQ:{f['nq_future']*100:+.2f}% | ES:{f['es_future']*100:+.2f}%")

    for group in GROUPS:
        p_base = calc_premium(group['base'], f)
        if p_base is None: continue

        for target in group['targets']:
            p_target = calc_premium(target, f)
            if p_target is None: continue

            spread = p_target - p_base
            alert_title = None
            alert_msg = None
            signal_type = ""

            # 进攻逻辑
            if spread < THRESHOLDS['ATTACK']:
                if p_target < THRESHOLDS['MAX_ABS_PREMIUM']:
                    signal_type = "进攻"
                    alert_title = f"⚔️ 进攻机会: {target['name']}"
                    alert_msg = (
                        f"<b>策略建议：卖出 {group['base']['name']} -> 买入 {target['name']}</b><br>"
                        f"📉 相对价差: <font color='green'>{spread:.2f}%</font><br>"
                        f"📊 目标真溢价: {p_target:.2f}% (安全)"
                    )
                else:
                    print(f"   🚫 {target['name']} 价差达标，但溢价{p_target:.2f}%过高，拦截")

            # 撤退逻辑
            elif spread > THRESHOLDS['RETREAT']:
                signal_type = "撤退"
                alert_title = f"🔥 撤退信号: {target['name']}"
                alert_msg = (
                    f"<b>策略建议：止盈 {target['name']} -> 回防 {group['base']['name']}</b><br>"
                    f"📈 相对价差: <font color='red'>{spread:.2f}%</font>"
                )

            # 发送逻辑
            if alert_title:
                key = f"{target['code']}_{signal_type}"
                current_count = alert_counts.get(key, 0)
                cooldown = 600 if current_count < 3 else 3600

                if key not in last_alert_time or (time.time() - last_alert_time[key] > cooldown):
                    print(f"🚀 发送报警: {alert_title}")
                    send_wechat(alert_title, alert_msg + f"<br><br><span style='color:gray'>今日第{current_count+1}次提醒</span>")
                    last_alert_time[key] = time.time()
                    alert_counts[key] = current_count + 1
                else:
                    print(f"   ⏳ {target['name']} {signal_type} 冷却中...")
            else:
                print(f"   💤 {target['name']} vs {group['base']['name']} | 价差: {spread:.2f}%")

if __name__ == "__main__":
    try:
        # 设置时区
        tz = pytz.timezone('Asia/Shanghai')
        print(f"🚀 云端监控 V11.8 永不掉线版启动...")
        
        while True:
            now = datetime.now(tz)
            
            # 1. 简单判断：周末不交易，但不要退出程序，而是进入休眠
            if now.weekday() > 4: 
                print(f"😴 周末休息中... ({now.strftime('%Y-%m-%d %H:%M')})")
                time.sleep(3600) # 睡1小时再来看
                continue
                
            # 2. 盘前盘后判断
            current_time = now.hour * 100 + now.minute
            
            # 开盘前 (9:15前)
            if current_time < 915:
                print(f"⏳ 等待开盘... ({now.strftime('%H:%M')})")
                time.sleep(300) # 睡5分钟
                continue
                
            # 收盘后 (15:15后) -> 这里改了！不再 break，而是 sleep
            if current_time > 1515: 
                print(f"😴 已收盘，进入待机模式... ({now.strftime('%H:%M')})")
                time.sleep(1800) # 睡半小时，防止频繁打印
                # 清除当日报警计数，方便第二天重新计数
                alert_counts = {} 
                last_alert_time = {}
                continue

            # 3. 盘中运行 (09:15 - 15:15)
            try:
                monitor_logic()
            except Exception as inner_e:
                print(f"⚠️ 轮询出错: {inner_e}")
            
            time.sleep(60) # 每分钟轮询一次

    except Exception as e:
        print("❌ 严重错误导致程序停止！")
        print(traceback.format_exc())
        input("按回车键退出...") # 这一行能防止闪退，让你看清报错