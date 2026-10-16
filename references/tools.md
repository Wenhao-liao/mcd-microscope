# mcd-mcp 工具速查与字段陷阱

服务器：`https://mcp.mcd.cn` · 协议：Streamable HTTP · 鉴权：`Authorization: Bearer <TOKEN>`
限流：600 次/分钟（超出返回 `429`）

截至 2026-10-16，实测 `tools/list` 返回 **35 个工具**。

> 注意：官方 README 里写的工具名与实际有出入（如 README 写 `query-partystore-date`，
> 实际是 `query-party-store-date`）。**以实测 `tools/list` 为准。**

---

## 全局规则：orderType 与 beType 的配对

这是最容易出错的地方。四个业务场景，参数组合完全不同：

| 场景 | beType | orderType | beCode |
|---|---|---|---|
| 到店自取 | 1 | 1 | **不传**（传了报错） |
| 得来速 DT | 5 | 1 | 必传（来自 `query-nearby-stores`） |
| 麦乐送 / 外送 | 2 | 2 | 必传（来自 `delivery-query-stores`） |
| 企业团餐 | 6 | 2 | 必传（来自 `delivery-query-stores`） |

预约场景额外传 `reservationDate`，格式 `yyyy-MM-dd HH:mm`；非预约场景**不要传**。

---

## 门店与地址

| 工具 | 用途 | 关键入参 |
|---|---|---|
| `query-nearby-stores` | 查可点餐门店 | `beType`(1/5), `searchType`(1=收藏/2=搜索), `city`, `keyword` |
| `delivery-query-stores` | 查可配送门店 | 外送场景 |
| `delivery-query-addresses` | 查配送地址列表 | — |
| `delivery-create-address` | 新增配送地址 | 写操作 |

**`query-nearby-stores` 返回字段**：
```
data[].storeName / address / distance / storeCode / beCode /
businessStatus / businessStartTime / businessEndTime /
reservation / reservationTimeOptions[]
```
- `searchType=1` 且无收藏时返回 `{"success":false,"code":600050}` —— 属正常，转 `searchType=2`。
- `reservationTimeOptions[]` 里的每一条都影响用户能否预约，**展示时不可省略**。
- `today=true` 的日期需标记出来。

---

## 菜单与商品

| 工具 | 用途 | 关键入参 |
|---|---|---|
| `query-meals` | 餐品列表（含价格） | `storeCode`, `orderType`, `beType` |
| `query-meal-detail` | 餐品详情 / 套餐组成 | 上列 + `code` |
| `list-nutrition-foods` | 营养库（无入参） | — |

**`query-meals` 返回结构**：
```
data.categories[].name            分类名
data.categories[].meals[].code    餐品编码
data.categories[].meals[].tags    标签
data.meals{code}.name             餐品名
data.meals{code}.image            图片 URL
data.meals{code}.currentPrice     现价（字符串，单位【元】）
data.meals{code}.originalPrice    原价（字符串，单位【元】）
data.meals{code}.discountType     优惠类型（null / 早餐卡 / 促销 / 麦金卡 / 随单购…）
data.meals{code}.canWithOrder     是否可随单购
data.frequent                     常点餐品
```

> ⚠️ **实测样本**：`storeCode=1420227` 返回的 `currentPrice` 与 `originalPrice` **完全相等**，
> 说明该店无划线促销价。**不要假设两者总有差异。**

**`query-meal-detail` 返回结构（套餐拆解的核心）**：
```
data.code / name / image / supportModify
data.rounds[].id / name / quantity / maxQuantity / minQuantity
data.rounds[].choices[].code            子项编码（可能与菜单同名单品 code 不同！）
data.rounds[].choices[].name            子项名
data.rounds[].choices[].quantity        已选数量
data.rounds[].choices[].isDefault       1 = 默认选中
data.rounds[].choices[].diffPrice       相对默认项的差价，如 "+ ¥0" / "+ ¥2.5"
data.rounds[].choices[].supportModify   是否可特调
data.rounds[].choices[].modification    特调项（含 selectedKey / unselectedKey）
```

> **`diffPrice` 为 `+ ¥0` 的非默认选项就是"不加钱可换项"** —— 这是本项目最重要的信息差来源。

**特调传参的铁律**：
- 选中的特调项 → 传 `selectedKey`
- 未选中的特调项，**若该项有 `unselectedKey`，也必须传**，值用 `unselectedKey`
- 即：含 `unselectedKey` 的特调组，**整组所有项都要传给算价/下单接口**
- 当特调组 `minValues=1 && maxValues=1` 且只有 1 项时，该项为必选，不能给用户"不选"的选项

**`list-nutrition-foods` 返回**：字符串形式的类 CSV，**158 项**，字段顺序：
```
productName, nutritionDescription, energyKj, energyKcal,
protein, fat, carbohydrate, sodium, calcium
```
- `nutritionDescription` 是官方自带的类比描述（如「能量约清水 1 杯」「能量约鲈鱼 1 条」），部分餐品为 `null`。
- **优先引用官方类比**，不要自己造比喻。

---

## 算价与下单

| 工具 | 用途 | 写操作 |
|---|---|---|
| `calculate-price` | 算价（含优惠） | 否 |
| `create-order` | 创建订单 | **是** |
| `cancel-order` | 取消订单 | **是** |

**`calculate-price` 返回结构（单位全部是【分】）**：
```
data.productOriginalPrice    商品原价
data.productPrice            商品现价
data.deliveryOriginalPrice   运费原价
data.deliveryPrice           运费现价
data.packingOriginalPrice    打包费原价
data.packingPrice            打包费现价
data.tablewarePrice          餐具费
data.originalPrice           总计原价
data.discount                优惠金额
data.price                   应付现价
data.productList[].productCode / productName / quantity / originalSubtotal / subtotal
data.takeWayList[].code / title / subtitle      取餐方式，下单需携带
data.mealAssistanceList[].code / name           助餐服务（团餐场景）
data.enjoyed.amountType / realDiscount / balance    已享受的优惠
data.enjoyable.amountType / realDiscount / balance  可享受但未达门槛的优惠
```
- `takeWayList` 实测样本：`eat-in`（堂食）/ `locker-in`（外带取餐柜）
- `enjoyable.balance` = 门槛差额（分）→ 可做"再买 X 元就能省 Y 元"提示
- `items[].couponId` / `couponCode` 用于指定用券

---

## 优惠券

| 工具 | 用途 |
|---|---|
| `query-my-coupons` | 我的券包（**候选集，不承诺可用**） |
| `query-store-coupons` | **指定门店**当前可用券（权威） |
| `available-coupons` | 麦麦省可领券列表 |
| `auto-bind-coupons` | 一键领取全部可领券（**写操作**） |

**`query-store-coupons` 返回结构**：
```
data[].title          券名
data[].couponId       券 ID（同一 ID 可对应多个券码，按 ID 分组）
data[].couponCode     券码
data[].tradeDateTime  有效期
data[].products[].productName / productCode   适用商品
```

> ⚠️ **`query-my-coupons` 的返回值不校验门店/渠道/配送方式**，
> 官方明确说明它"不承诺可用于当前订单"。要做"这单能用什么券"必须走 `query-store-coupons`。

---

## 积分、商城与抽奖

| 工具 | 用途 |
|---|---|
| `query-my-account` | 积分账户（无入参） |
| `mall-points-products` | 积分商城商品列表 |
| `mall-product-detail` | 商城商品详情 |
| `mall-create-order` | 积分兑换下单（**写操作**） |
| `mall-order-list` / `mall-order-detail` | 商城订单查询 |
| `query-lottery-info` | 抽奖活动信息 |
| `draw-lottery` | 执行抽奖（**写操作**） |
| `query-my-prizes` | 我的奖品 |

**`query-my-account` 返回字段**：
```
data.availablePoint             可用积分
data.accumulativePoint          累计积分
data.frozenPoint                冻结积分
data.expiredPoint               已过期作废积分
data.usedPoint                  已使用积分
data.currentMouthExpirePoint    本月将过期
data.nextMouthExpirePoint       下月将过期
data.lastMouthExpirePoint       上月已过期
data.currency                   货币类型（如"麦享会积分"）
```
- 积分值是**字符串**，可能带小数（如 `"1125.7"`）。
- 注意官方字段名拼写是 `Mouth`（非 `Month`），**照抄不要改**。

**`mall-points-products` 可选类目 `catRuleIds`**（逗号分隔）：
```
1>4      商品券（非预付优惠券）
2        实物商品
2>8      周边产品
2>9      实物礼品卡
1>6>20   生日类派对
1>6>21   主题类派对
1>6>22   麦麦体验营
1>6>25   品鉴会
1>6>34   读书会
1>6>40   积分兑换活动
```

---

## 活动与派对

| 工具 | 用途 |
|---|---|
| `campaign-calendar` | 当月营销活动日历（可传 `specifiedDate` 取前后各一天） |
| `query-party-city` | 主题活动可参与城市 |
| `query-party-store` | 指定城市下的门店 |
| `query-party-store-date` | 指定门店的可预约日期 |
| `query-party-store-session` | 指定日期下的场次 |
| `party-order-create` | 主题活动下单（**写操作**） |

**`campaign-calendar` 返回的是排版好的富文本**（含标题、活动正文、海报图片 HTML），
**不是结构化 JSON**。文案里的价格需要做文本解析，且要注意：
- 海报价通常带前置条件（凭券 / 麦金卡 / 早餐卡 / APP 专享积分）
- 活动描述里可能有 emoji 和全角字符（如 `𝟗.𝟗`），解析时要归一化

---

## 订单

| 工具 | 用途 |
|---|---|
| `order-list` | 历史订单（到店/外送，**不含商城**） |
| `query-order` | 订单详情 |
| `mall-order-list` | 商城订单列表（近一年） |
| `mall-order-detail` | 商城订单详情 |

---

## 其他

| 工具 | 用途 |
|---|---|
| `now-time-info` | 服务器时间（零副作用，适合做连通性自检） |
| `query-meal-assistance` | 团餐助餐服务（仅企业团餐场景） |
| `query-promotions` | 促销信息 |
| `query-survey-coupon` | 问卷券 |

---

## 错误码

| code | 含义 | 处理 |
|---|---|---|
| `200` | 成功 | — |
| `600022` | 一单仅支持一张券 | 不可叠券，改为单张最优券策略 |
| `600050` | 收藏门店为空 | 转 `searchType=2` 搜索 |
| `401` | Token 无效 / 过期 / 未提供 | 重新申请 MCP Token |
| `429` | 触发限流（>600 次/分钟） | 降低频率后重试 |

> 返回体里 `success` 字段是会变的（成功为 `true`），**判断成败优先看 `code`**。
