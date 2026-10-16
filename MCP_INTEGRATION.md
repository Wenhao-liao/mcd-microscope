# MCP 集成说明

本项目基于**麦当劳中国官方 MCP Server**（`mcd-mcp`）开发，所有数据均通过 MCP 工具实时获取，**不做任何本地硬编码或人工估算**。

---

## 一、接入的 MCP Server

| 项 | 值 |
|---|---|
| Server 名称 | `mcd-mcp`（麦当劳中国官方 MCP Server） |
| 服务地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 工具数量 | 实测 `tools/list` 返回 **35 个** Tool |
| 限流 | 600 次/分钟（超出返回 429），Token 失效返回 401 |
| 官方文档 | https://github.com/M-China/mcd-mcp-server |

Token 由使用者自行在 https://open.mcd.cn/mcp 申请，**不随仓库分发**，配置文件仅使用环境变量占位符（见 `mcp-config.example.json`）。

---

## 二、实际调用的 Tool 与调用流程

项目按 **6 个分析维度**组织（与 `SKILL.md` 的「第 2 步：六大分析维度」一一对应）。其中 **F 为核心模块**。

### 维度 A · 套餐拆解

**回答**：这个套餐到底比单点便宜多少？

**Tool**：`query-meal-detail` → `calculate-price`（两次）

```
1. query-meal-detail(storeCode, orderType=1, beType=1, code=套餐code)
                       → 拿 rounds[]，每个 round 里 isDefault=1 的 choice 即默认组合
2. calculate-price(items=[{套餐code, 1}])                      → 套餐价
3. calculate-price(items=[{子项1,1},{子项2,1},{子项3,1}])       → 单点合计价
4. 两者相减，得到真实省幅
```

**业务价值**：把"套餐"这个词的心理暗示还原成数字。实测结论——巨无霸三件套 ¥54.50，而巨无霸+中薯条+可乐单点合计 ¥55.00，**只省 ¥0.50**。

> **关键陷阱**：套餐子项的 code 与菜单里同名单品的 code **不一致**（套餐内「可乐中杯」是 `3050`，菜单里「可乐」是 `9900008751`）。必须用 round 里给的 code，找不到才退回菜单同名单品并在报告中标注口径。

### 维度 B · 广告价 vs 到手价

**回答**：海报上那个价，我今天真的能拿到吗？

**Tool**：`campaign-calendar` → `query-meals` → `query-store-coupons`

```
1. campaign-calendar()                          拉本月活动原文（含海报文案里的宣传价）
   campaign-calendar(specifiedDate="yyyy-MM-dd") 只看某天前后
2. query-meals(storeCode, orderType=1, beType=1) 拉该门店同名单品的无券标价
3. query-store-coupons(storeCode, orderType, beType) 校验该门店当前真正可用的券
4. 输出「达成海报价需要什么条件」与「无条件时的真实价格」
```

**业务价值**：海报价几乎都带**前置条件**（凭券 / 麦金卡 / 早餐卡 / APP 专享积分抢购）。项目做的不是"拆穿"，而是把条件讲清楚——**有券是多少，没券是多少**。

> **关键陷阱**：`campaign-calendar` 返回的是排版好的富文本，**不是结构化 JSON**，解析价格时要当心全角数字字符。另需注意 `query-my-coupons`（券包候选集）与 `query-store-coupons`（门店可用性校验）语义不同——**已领取 ≠ 门店可用**。

### 维度 C · 换购暗门

**回答**：套餐里有哪些东西，不加钱就能换？

**Tool**：`query-meal-detail`

遍历每个 round 的 `choices[]`，筛出所有 **`diffPrice` 为 `+ ¥0`** 的非默认选项。

**业务价值**：实测发现中薯条可换小杯玉米杯、可乐中杯可换无糖可乐 / 雪碧中杯，**均为 +¥0**，且菜单与点餐入口都不提示。换成无糖可乐：价格不变，糖归零，热量少 147 kcal。

> 换购项的营养数据同样要走口径对齐，匹配不上就标未知。

### 维度 D · 积分寿命

**回答**：我的积分会不会白白过期？

**Tool**：`query-my-account` → `mall-points-products`

```
1. query-my-account()       → availablePoint / currentMouthExpirePoint /
                              nextMouthExpirePoint / expiredPoint 等
2. mall-points-products()   → 积分能换什么（可传 catRuleIds 筛选类目）
```

**业务价值**：算三笔账——**余额**、**寿命**（`currentMouthExpirePoint` 本月将过期，是行动优先级最高的数字）、**历史浪费**（`expiredPoint` 已过期作废）。再用 `mall-points-products` 里同类商品的积分价 vs 现金价反算「1 积分 ≈ 多少元」。

> **关键陷阱**：官方字段名拼写是 `currentMouthExpirePoint`（`Mouth` 而非 `Month`），照抄不要"纠正"。样本不足时价值折算要标注为估算口径，不给假精确的数字。

### 维度 E · 热量账

**回答**：这一单吃下去多少热量？

**Tool**：`list-nutrition-foods`

用 `energyKcal` 给指定组合算总热量。部分餐品官方自带 `nutritionDescription` 类比（如「能量约清水 1 杯」），**优先直接引用官方的类比**，比自己造比喻更稳。换算运动量时必须说明假设（体重、配速），否则只给 kcal。

### 维度 F · 营养性价比地图（核心模块）

**回答**：同样花 15 块，买哪一项的蛋白最划算？

**Tool**：`query-meals` → `list-nutrition-foods` → 本地计算

```
1. query-meals(storeCode, orderType=1, beType=1)  拉门店全量菜单与标价（元，字符串）
2. list-nutrition-foods()                          拉官方营养表（类 CSV 文本，不是 JSON）
3. 名称归一化后匹配，计算三个指标并排输出：
      g 蛋白 / 元        性价比
      g 蛋白 / 100kcal   蛋白密度（减脂视角）
      钠 mg              负荷约束
4. 按目标（增肌 / 减脂 / 控钠）分流输出
```

**业务价值**：把度量单位从「多少钱」换成「**每 1 元买到多少克蛋白质**」。实测该门店排行第一是 **¥15 的那么大鸡排（1.600 g/元）**，比第二名高 57%；而公认"健身友好"的麦香鸡、麦香鱼反而垫底。

> **口径对齐是本模块的硬约束**：官方营养表按「**份**」给（「麦乐鸡5块」「麦乐鸡4块」是两行），菜单按「**品**」给（只有一个「麦乐鸡」），两者不是一一对应。实测该门店 94 个单品中，**25 项无对应营养数据、16 项规格/份数不明**——这些**全部排除，未做任何估算或折算**，最终 **53 项进入排行**。
>
> 另外，麦辣鸡腿汉堡虽性价比排第 6，钠却高达 **1208mg**（接近每日推荐上限一半）。因此本项目**三项指标并排输出**，不做单一排行榜——单指标排序会把人带偏。

**参考实现**：`scripts/compute_protein_value.py`（可独立运行，换任意门店重跑）

```bash
python3 scripts/compute_protein_value.py [storeCode] [--density] [--json out.json]
```

---

## 三、一次完整调用的真实链路

以「增肌的话在麦当劳该点哪个」为例：

```
用户提问
  └─ query-nearby-stores          → 解析用户所在门店，得到 storeCode
  └─ query-meals                  → 拉该门店全量菜单 + 标价（单位：元）
  └─ list-nutrition-foods         → 拉官方营养表（类 CSV 文本）
  └─ [本地] compute_protein_value.py
       ├─ 名称归一化（去括号内容 / 去符号 / 大写化）
       ├─ 匹配：精确 → 双向包含 → ambiguous（份数不明） → miss
       ├─ 排除套餐（tags 判断 + 名称提示词双重过滤）
       ├─ 计算 g蛋白/元、g蛋白/100kcal、钠
       └─ 按指标排序输出
  └─ 渲染为「营养性价比地图」卡片
```

以「帮我把巨无霸三件套拆开看看值不值」为例：

```
  └─ query-meals            → 找到「巨无霸三件套」的 code
  └─ query-meal-detail      → 拿 rounds[] / choices[] / diffPrice
  └─ calculate-price ×2     → 套餐价 vs 单点合计价
  └─ list-nutrition-foods   → 补上热量与钠
  └─ 渲染为「麦门成分表」卡片
```

---

## 四、工程上解决的三个 MCP 适配问题

这三个问题不解决项目跑不起来，且都已固化进 `references/tools.md` 与 `SKILL.md`：

**1. Tool 返回是「说明文本 + 原始 JSON」的混合体，不能直接 `json.loads`。**
`tools/call` 的 `content[0].text` 前半段是给模型看的说明文字，后半段才是 JSON。解法是在 `## Original Response` 锚点之后用 `JSONDecoder.raw_decode` 逐位置试探，取第一个含 `code` 字段的 dict。例外：`campaign-calendar` 返回的是富文本，本身就不是 JSON。

**2. 金额单位在同一个 Server 内不统一。**
`query-meals` 是**元**（字符串），`calculate-price` 是**分**（整数）。项目中统一在边界处 ÷100，展示层统一用元、保留两位小数。**任何金额都必须由接口算出，找不到就标「未知」，不猜。**

**3. 入参存在强配对关系。**
`orderType` / `beType` / `beCode` 三者必须配套：

| 场景 | beType | orderType | beCode |
|---|---|---|---|
| 到店自取 | 1 | 1 | **不能传**（传了直接报错） |
| 得来速 | 5 | 1 | 必传 |
| 麦乐送 | 2 | 2 | 必传 |
| 团餐 | 6 | 2 | 必传 |

---

## 五、安全与合规边界

- **只读优先**：项目的 6 个分析维度**全部是只读调用**，不涉及任何写操作。
- **不代付**：涉及 `create-order` / `mall-create-order` / `party-order-create` 时，必须在用户明确确认后才执行，且只把官方支付链接交给用户，**不由本项目代为支付**。
- **不落地敏感数据**：`query-my-account` 等涉及个人账户的返回**不写入仓库**，`examples/` 中的实测存档已做脱敏处理。
- **只呈现数据，不给营养或医疗建议**：项目输出是官方营养表与菜单标价的**事实性汇总**，不构成营养学、医学或任何专业建议。
- **中立叙事**：定位是「把官方已公开的数据算清楚」，不做对抗性表述，也不将麦当劳产品与其他品牌对比。

---

## 六、业务价值小结

| 维度 | 解决的问题 | 靠哪些 Tool |
|---|---|---|
| A · 套餐拆解 | 套餐到底便宜多少 | `query-meal-detail` + `calculate-price` |
| B · 广告价 vs 到手价 | 海报价的达成条件；券能不能用 | `campaign-calendar` + `query-meals` + `query-store-coupons` |
| C · 换购暗门 | 不加钱能换什么 | `query-meal-detail` |
| D · 积分寿命 | 积分会不会白白过期 | `query-my-account` + `mall-points-products` |
| E · 热量账 | 这一单多少热量 | `list-nutrition-foods` |
| F · 营养性价比地图 | 哪一项蛋白最划算 | `query-meals` + `list-nutrition-foods` |

**一句话**：这些数据麦当劳官方全都开放了接口，但过去没有一个工具替用户把它们摊开算清楚。本项目做的就是这件事——**每一条结论都有接口实测支撑，且踩过的坑都留在了文档里。**
