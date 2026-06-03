"""Common package id ↔ display name aliases for `open` actions (Android + HarmonyOS)."""

from __future__ import annotations

# Tab-separated rows: bundle/package_id, alias1, alias2, ...
# Android (adb transport) entries
_PACKAGE_ALIAS_LINES = r"""com.tencent.mm	微信	wechat
com.tencent.mobileqq	qq	腾讯qq
com.sina.weibo	微博
com.taobao.taobao	淘宝
com.jingdong.app.mall	京东	京东秒送
com.xunmeng.pinduoduo	拼多多
com.xingin.xhs	小红书
com.douban.frodo	豆瓣
com.zhihu.android	知乎
com.autonavi.minimap	高德地图	高德
com.baidu.BaiduMap	百度地图
com.sankuai.meituan.takeoutnew	美团外卖
com.sankuai.meituan	美团	美团外卖
com.dianping.v1	大众点评	点评
me.ele	饿了么	淘宝闪购
com.yek.android.kfc.activitys	肯德基
ctrip.android.view	携程	携程旅行
com.MobileTicket	铁路12306	12306
com.Qunar	去哪儿旅行	去哪儿网	去哪儿
com.sdu.didi.psnger	滴滴出行	滴滴
tv.danmaku.bili	bilibili	b站	哔哩哔哩	哔站	bili
com.ss.android.ugc.aweme	抖音
com.smile.gifmaker	快手
com.tencent.qqlive	腾讯视频
com.qiyi.video	爱奇艺
com.youku.phone	优酷	优酷视频
com.hunantv.imgo.activity	芒果tv	芒果
com.phoenix.read	红果短剧	红果
com.netease.cloudmusic	网易云音乐	网易云
com.tencent.qqmusic	qq音乐
com.luna.music	汽水音乐
com.ximalaya.ting.android	喜马拉雅
com.dragon.read	番茄免费小说	番茄小说
com.kmxs.reader	七猫免费小说
com.ss.android.lark	飞书
com.tencent.androidqqmail	qq邮箱
com.larus.nova	豆包	豆包
com.gotokeep.keep	keep
com.lingan.seeyou	美柚
com.tencent.news	腾讯新闻
com.ss.android.article.news	今日头条
com.lianjia.beike	贝壳找房
com.anjuke.android.app	安居客
com.hexin.plat.android	同花顺
com.miHoYo.hkrpg	星穹铁道	崩坏
com.papegames.lysk.cn	恋与深空
com.android.settings	settings	androidsystemsettings
com.android.soundrecorder	audiorecorder
com.rammigsoftware.bluecoins	bluecoins
com.flauschcode.broccoli	broccoli
com.booking	booking
com.android.chrome	谷歌浏览器	googlechrome	chrome
com.android.deskclock	时钟	闹钟	clock
com.android.contacts	contacts
com.duolingo	duolingo	多邻国
com.expedia.bookings	expedia
com.android.fileexplorer	files	filemanager
com.google.android.gm	gmail	googlemail
com.google.android.apps.nbu.files	googlefiles	filesbygoogle
com.google.android.calendar	googlecalendar
com.google.android.apps.dynamite	googlechat
com.google.android.deskclock	googleclock
com.google.android.contacts	googlecontacts
com.google.android.apps.docs.editors.docs	googledocs
com.google.android.apps.docs	googledrive
com.google.android.apps.fitness	googlefit
com.google.android.keep	googlekeep
com.google.android.apps.maps	googlemaps
com.google.android.apps.books	googleplaybooks
com.android.vending	googleplaystore
com.google.android.apps.docs.editors.slides	googleslides
com.google.android.apps.tasks	googletasks
net.cozic.joplin	joplin
com.mcdonalds.app	麦当劳	mcdonald
net.osmand	osmand
com.Project100Pi.themusicplayer	pimusicplayer
com.quora.android	quora
com.reddit.frontpage	reddit
code.name.monkey.retromusic	retromusic
com.scientificcalculatorplus.simplecalculator.basiccalculator.mathcalc	simplecalendarpro
com.simplemobiletools.smsmessenger	simplesmsmessenger
org.telegram.messenger	telegram
com.einnovation.temu	temu
com.zhiliaoapp.musically	tiktok
com.twitter.android	twitter	x
org.videolan.vlc	vlc
com.whatsapp	whatsapp
com.taobao.movie.android	淘票票
com.tongcheng.android	同程旅行	同程
com.sankuai.movie	猫眼
com.wuba.zhuanzhuan	转转
com.tencent.weread	微信读书
com.taobao.idlefish	闲鱼
com.wudaokou.hippo	盒马
com.eg.android.AlipayGphone	支付宝
com.jd.jrapp	京东金融
com.achievo.vipshop	唯品会
com.smzdm.client.android	什么值得买
cn.kuwo.player	酷我音乐
com.taobao.trip	飞猪	飞猪旅行
com.jingdong.pdj	京东到家
com.tencent.map	腾讯地图
com.shizhuang.duapp	得物
cn.damai	大麦	大麦网
com.ss.android.auto	懂车帝
com.cubic.autohome	汽车之家
com.wuba	58同城	五八同城
com.android.calendar	日历
com.alibaba.android.rimet	钉钉
com.meituan.retail.v.android	小象超市
com.aliyun.tongyi	通义	千问	通义千问
com.hupu.games	虎扑	虎扑体育
com.quark.browser	夸克	夸克浏览器
com.yuantiku.tutor	猿辅导
com.tencent.mtt	qq浏览器
com.umetrip.android.msky.app	航旅纵横
com.UCMobile	UC浏览器
com.ss.android.ugc.aweme.lite	抖音极速版	抖音
air.tv.douyu.android	斗鱼
com.tencent.hunyuan.app.chat	元宝
com.baidu.searchbox	百度
com.lemon.lv	剪映
cn.soulapp.android	soul
com.baidu.netdisk	百度网盘
com.tmri.app.main	交管12123	12123
com.kugou.android	酷狗	酷狗音乐
com.tencent.android.qqdownloader	应用宝
com.mt.mtxx.mtxx	美图	美图秀秀
com.tencent.karaoke	全民k歌
com.intsig.camscanner	扫描全能王
com.android.bankabc	农业银行	农行
cmb.pb	招商银行	招行
com.ganji.android.haoche_c	瓜子二手车	瓜子
com.sf.activity	顺丰	顺丰快递	顺丰速运
com.ziroom.ziroomcustomer	自如
com.yumc.phsuperapp	必胜客
cn.dominos.pizza	达美乐披萨	达美乐
cn.wps.moffice_eng	WPS Office	WPS
com.mfw.roadbook	马蜂窝
com.moonshot.kimichat	kimi
com.tencent.wemeet.app	腾讯会议
com.deepseek.chat	deepseek
com.spdbccc.app	浦发银行
cn.samsclub.app	山姆超市	山姆	山姆会员商店	山姆会员店
com.tencent.qqsports	腾讯体育
com.hanweb.android.zhejiang.activity	浙里办
com.ss.android.article.video	西瓜视频
com.taou.maimai	脉脉"""

# HarmonyOS (hdc transport) bundle name entries
_HARMONY_BUNDLE_ALIAS_LINES = r"""com.huawei.hmos.settings	设置	华为设置	settings
com.huawei.hmos.camera	相机	摄像头	camera
com.huawei.hmos.photos	图库	相册	photos	gallery
com.huawei.hmos.browser	浏览器	华为浏览器	browser
com.huawei.hmos.email	邮件	华为邮件	email
com.huawei.hmos.calendar	日历	calendar
com.huawei.hmos.clock	时钟	闹钟	clock
com.huawei.hmos.calculator	计算器	calculator
com.huawei.hmos.notepad	备忘录	笔记	notepad	notes
com.huawei.hmos.filemanager	文件管理器	文件管理	filemanager
com.huawei.hmos.files	文件	files
com.huawei.hmos.soundrecorder	录音机	录音	recorder
com.huawei.hmos.screenrecorder	屏幕录制	录屏	screenrecorder
com.huawei.hmos.videoplayer	视频播放器	视频	videoplayer
com.huawei.hmos.maps.app	地图	华为地图	map	maps
com.huawei.hmos.health	运动健康	健康	health
com.huawei.hmos.wallet	钱包	华为钱包	wallet
com.huawei.hmos.clouddrive	云空间	华为云	clouddrive
com.huawei.hmos.meetime	畅联	视频通话	meetime
com.huawei.hmos.myhuawei	我的华为	myhuawei
com.huawei.hmos.vmall	华为商城	商城	vmall
com.huawei.hmos.hicar	HiCar	hicar
com.huawei.hmos.vassistant.launcher	小艺	语音助手	assistant
com.huawei.hmos.inputmethod	小艺输入法	输入法	inputmethod
com.huawei.hmos.applock	应用锁	applock
com.huawei.hmos.databackup	数据和恢复	备份	backup
com.huawei.hmos.instantshare	华为分享	分享	instantshare
com.huawei.hmos.hisuite	华为手机助手	hisuite
com.huawei.hmsapp.appgallery	应用市场	应用商店	appgallery
com.huawei.hmsapp.music	音乐	华为音乐	music
com.huawei.hmsapp.himovie	视频	华为视频	himovie
com.huawei.hmsapp.books	阅读	华为阅读	books	reading
com.huawei.hmsapp.compass	指南针	compass
com.huawei.hmsapp.gamecenter	游戏中心	gamecenter
com.huawei.hmsapp.thememanager	主题	themes
com.huawei.hmsapp.totemweather	天气	weather
com.huawei.hms.weather	天气服务	weatherservice
com.ohos.contacts	联系人	contacts
com.ohos.mms	信息	短信	mms	messages
com.ohos.callui	电话	拨号	phone	dialer
cn.wps.mobileoffice.hap	WPS	WPS移动版	wps
com.alipay.mobile.client	支付宝	alipay
com.baidu.baiduapp	百度	baidu
com.dragon.read.next	番茄免费小说	番茄小说	fanqie
com.fliggy.hmos	飞猪旅行	飞猪	fliggy
com.jd.hm.mall	京东	jd
com.kuaishou.hmapp	快手	kuaishou
com.phoenix.read.next	红果短剧	红果	honguo
com.quark.ohosbrowser	夸克	夸克浏览器	quark
com.qunar.hos	去哪儿旅行	去哪儿	qunar
com.ss.hm.article.news	今日头条	头条	toutiao
com.ss.hm.ugc.aweme	抖音	douyin
com.taobao.idlefish4ohos	闲鱼	xianyu
com.taobao.taobao4hmos	淘宝	taobao
com.tongcheng.hmos	同程旅行	同程	tongcheng
com.umetrip.pro.hm.app	航旅纵横Pro	航旅纵横	umetrip
com.vip.hosapp	唯品会	vipshop
com.xingin.xhs_hos	小红书	xhs
com.xunmeng.pinduoduo.hos	拼多多	pdd"""


def normalize_alias_key(name: str) -> str:
    return name.lower().strip().replace(" ", "").replace("-", "")


def _parse_table(raw: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if not parts:
            continue
        pkg = parts[0].strip()
        if not pkg:
            continue
        for raw_alias in parts[1:]:
            s = raw_alias.strip()
            if not s:
                continue
            nk = normalize_alias_key(s)
            if nk not in result:
                result[nk] = [pkg]
            elif pkg not in result[nk]:
                result[nk].append(pkg)
    return result


# Per-transport alias dicts: transport name → {normalized_alias → [pkg, ...]}
# Kept separate so resolve_open_candidates can return transport-native packages
# first instead of mixing Android and HarmonyOS bundle names in one list.
_TRANSPORT_DICTS: dict[str, dict[str, list[str]]] = {
    "adb": _parse_table(_PACKAGE_ALIAS_LINES),
    "hdc": _parse_table(_HARMONY_BUNDLE_ALIAS_LINES),
}

# Task-injected overrides (apply on top of transport dicts), keyed by transport.
_TASK_OVERRIDES: dict[str, dict[str, list[str]]] = {}


def register_task_apps(apps: list[dict], transport: str = "adb") -> None:
    """Inject task-specific {name, package} entries for a given transport.

    Entries from the task's apps list take priority over built-in aliases so
    that open("Mail") resolves to the task-specific package rather than any
    generic alias.  Existing entries for the same normalized key are replaced.
    """
    overrides = _TASK_OVERRIDES.setdefault(transport, {})
    for app in apps:
        name = str(app.get("name") or "").strip()
        pkg = str(app.get("package") or "").strip()
        if not name or not pkg:
            continue
        nk = normalize_alias_key(name)
        overrides[nk] = [pkg]


def resolve_open_candidates(app_query: str, transport: str = "adb") -> list[str]:
    """Return ordered package id candidates for an ``open`` text query.

    *transport* selects which alias table to consult (``"adb"`` for Android,
    ``"hdc"`` for HarmonyOS).  Task-injected overrides for the same transport
    take precedence over built-in aliases.
    """
    q = app_query.strip()
    if not q:
        return []
    if "." in q and "/" not in q and " " not in q:
        return [q]
    nk = normalize_alias_key(q)
    overrides = _TASK_OVERRIDES.get(transport, {})
    if nk in overrides:
        return list(overrides[nk])
    table = _TRANSPORT_DICTS.get(transport, {})
    return list(table.get(nk, []))

