# SQL 入门：从查询到汇总

这是为学习环公开演示原创编写的短教材。表名、人物和数字均为虚构，可用于独立测试。

## 示例数据

| name | topic | minutes |
| --- | --- | --- |
| 学习者甲 | SQL | 20 |
| 学习者乙 | SQL | 30 |
| 学习者甲 | Python | 15 |

假设这些记录存储在 study_sessions 表中。

## 选择列

`SELECT name, minutes FROM study_sessions;` 只返回姓名与时长。SELECT 选择列，FROM 指定来源表。查询本身不会修改记录。

自测：如果只想看到学习主题，应选择哪一列？

## 筛选记录

`SELECT name, minutes FROM study_sessions WHERE minutes >= 20;` 保留至少学习20分钟的记录。本例返回两行。WHERE 条件作用在每一行上。

自测：如果把条件改成 minutes > 20，会返回几行？为什么？

## 按类别汇总

`SELECT topic, SUM(minutes) FROM study_sessions GROUP BY topic;` 将相同主题的记录分组，再对每组学习时长求和。SQL为50分钟，Python为15分钟。

自测：WHERE 与 GROUP BY 分别在解决什么问题？

## 复习建议

合上材料，用自己的话解释三个关键字。第二天先回忆，再检查遗漏。把具体查询写出来，比只判断“看起来熟悉”更容易发现理解中的空缺。
