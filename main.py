import requests
import json
import time
from datetime import datetime
import pytz # 必须依赖 pytz 来处理时区
import urllib3

urllib3.disable_warnings()

# ==========================================
# 🎯 V7.0 终极云端版：精准控时 + 微信报警
# ==========================================

# 🔴🔴🔴 请在此处填入你的 PushPlus Token 🔴🔴🔴
PUSHPLUS_TOKEN = '229e6e58116042c8a0065709dd98eabc' 

# 策略配置 (复刻博主逻辑)
STRATEGY_CONFIG = {
    "ATTACK_THRESHOLD": 0.6,  # 进攻：价差小于 0.6% -> 买入
    "RETREAT_THRESHOLD": 2.5  # 撤退：价差大于 2.5% -> 卖出
}

PAIRS = [
    {
        "group": "纳指组",
        "my":     {"code": "159659", "name": "我的国泰", "symbol": "sz159659", "offset": 0.18, "index": "gb_ndx", "future": "NQ"},
        "target": {"code": "159501", "name": "目标嘉实", "symbol": "sz159501", "offset": 0.18, "index": "gb_ndx", "future": "NQ"}
    },
    {
        "group": "标普组",
        "my":     {"code": "159655", "name": "我的华夏", "symbol": "sz159655", "offset": 0.22, "index": "gb_inx", "future": "ES"},
        "target": {"code": "513500", "name": "目标博时", "symbol": "sh513500", "offset": 0.22, "index": "gb_inx", "future": "ES"}
    }
]

# 全局变量：记录上次报警时间，防止微信轰炸
last_alert_time = {}

def send_wechat(title, content):
    """发送微信通知"""
    url = 'http://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "html"
    }
    try:
        requests.post(url, json=data, timeout=5)
        print(f"✅ [微信发送成功] {title}")
    except Exception as e:
        print(f"❌ [微信发送失败] {e}")

def get_market_factors():
    """获取美股期货和汇率"""
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
    except:
        return None

def calc_premium(conf, factors):
    """计算单只基金的实时溢价率"""
    try:
        # 1. 抓取腾讯现价
        r_p = requests.get(f"http://qt.gtimg.cn/q={conf['symbol']}", timeout=2)
        p_vals = r_p.content.decode('gbk', errors='ignore').split('~')
        price = float(p_vals[3]) if float(p_vals[3]) > 0 else float(p_vals[4])
        
        # 2. 抓取净值
        ts = int(time.time() * 1000)
        r_n = requests.get(f"http://fundgz.1234567.com.cn/js/{conf['code']}.js?rt={ts}", timeout=2)
        # 解析 jsonpgz({...});
        start = r_n.text.find('(') + 1
        end = r_n.text.rfind(')')
        if start <= 0 or end <= 0: return None
        nav_data = json.loads(r_n.text[start:end])
        nav = float(nav_data['dwjz'])

        # 3. 计算精细 IOPV
        close_pct = factors['inx_close'] if conf['index'] == 'gb_inx' else factors['ndx_close']
        future_pct = factors['es_future'] if conf['future'] == 'ES' else factors['nq_future']
        
        iopv = nav * (1 + close_pct) * (1 + future_pct) * (1 + factors['usd_cnh'])
        return (price - iopv) / iopv * 100 + conf['offset']
    except Exception:
        return None

def monitor_logic():
    """核心监控逻辑"""
    f = get_market_factors()
    if not f: 
        print("⚠️ 无法获取市场因子，跳过本次循环")
        return

    # 打印心跳日志 (GitHub 后台看得到)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] NQ:{f['nq_future']*100:+.2f}% | ES:{f['es_future']*100:+.2f}%")

    for pair in PAIRS:
        p_my = calc_premium(pair['my'], f)
        p_target = calc_premium(pair['target'], f)

        if p_my is not None and p_target is not None:
            spread = p_target - p_my
            
            # --- 判定信号 ---
            alert_msg = None
            alert_title = None
            
            # 1. 进攻信号
            if spread < STRATEGY_CONFIG['ATTACK_THRESHOLD']:
                alert_title = f"⚔️ 进攻信号: {pair['group']}"
                alert_msg = (f"<h2 style='color:red'>建议切换: 卖{pair['my']['name']} -> 买{pair['target']['name']}</h2>"
                             f"<p>当前价差: <b>{spread:.2f}%</b> (小于阈值 {STRATEGY_CONFIG['ATTACK_THRESHOLD']}%)</p>"
                             f"<p>我的持仓溢价: {p_my:.2f}%</p>"
                             f"<p>目标溢价: {p_target:.2f}%</p>")
            
            # 2. 撤退信号
            elif spread > STRATEGY_CONFIG['RETREAT_THRESHOLD']:
                alert_title = f"🛡️ 撤退信号: {pair['group']}"
                alert_msg = (f"<h2 style='color:green'>建议收网: 卖{pair['target']['name']} -> 回{pair['my']['name']}</h2>"
                             f"<p>当前价差: <b>{spread:.2f}%</b> (大于阈值 {STRATEGY_CONFIG['RETREAT_THRESHOLD']}%)</p>"
                             f"<p>我的持仓溢价: {p_my:.2f}%</p>"
                             f"<p>目标溢价: {p_target:.2f}%</p>")
            
            # --- 发送报警 (带冷却时间) ---
            if alert_title:
                key = f"{pair['group']}_{alert_title}"
                # 冷却时间：15分钟内不重复报同一个警
                if key not in last_alert_time or (time.time() - last_alert_time[key] > 900):
                    print(f"🔥 触发报警: {alert_title}")
                    send_wechat(alert_title, alert_msg)
                    last_alert_time[key] = time.time()
            else:
                # 没信号时只在后台打印
                print(f"   💤 {pair['group']} 价差 {spread:.2f}% (无操作)")

if __name__ == "__main__":
    # 设置北京时区
    tz = pytz.timezone('Asia/Shanghai')
    print("🚀 云端监控脚本启动...")
    
    # 稍微测试一下微信推送是否通畅 (可选，不想每次启动都发就注释掉)
    # send_wechat("脚本上线通知", f"监控已启动，当前北京时间: {datetime.now(tz).strftime('%H:%M')}")

    while True:
        now = datetime.now(tz)
        current_time_int = now.hour * 100 + now.minute # 例如 930 代表 9:30
        
        # 1. 判断是否是周末 (0=周一, 6=周日)
        if now.weekday() > 4:
            print("💤 今天是周末，不工作。脚本退出。")
            break
            
        # 2. 判断是否收盘 (超过 15:15)
        if current_time_int > 1515:
            print("👋 已过 15:15，A股收盘，下班！")
            break
            
        # 3. 判断是否开盘 (09:15 - 15:15)
        if 915 <= current_time_int <= 1515:
            try:
                monitor_logic()
            except Exception as e:
                print(f"⚠️ 运行出错 (自动重试): {e}")
            
            # ⏳ 核心：每 60 秒刷新一次
            time.sleep(60)
            
        else:
            # 还没到 9:15，休眠等待
            print(f"⏳ 还没开盘 (当前 {now.strftime('%H:%M')})，等待中...")
            time.sleep(60)