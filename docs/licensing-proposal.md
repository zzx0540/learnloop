# 许可证方案：AGPL-3.0-only（已确认）

## 已采用的方案

在保留当前 PDF / EPUB 解析实现的前提下，作者已于2026-10-20确认将学习环公开版本的自有代码按 **GNU Affero General Public License version 3 only（SPDX: AGPL-3.0-only）** 授权。

根目录 LICENSE 保留 AGPLv3 完整条款；NOTICE、README 和包元数据明确限定 version 3 only。作者同时授权在最终检查和 CI 通过后转为公开。第三方组件与字体保持各自许可。

## 依据

| 现有依赖 | 本轮核对版本 | 许可信息 |
| --- | --- | --- |
| PyMuPDF | 1.28.2 | 已安装分发包 COPYING 标明 AGPL 3.0 / Artifex 商业许可双授权 |
| pymupdf4llm | 0.0.27 | 分发包元数据标明 AGPL / 商业许可；上游提供 AGPLv3 文本 |
| EbookLib | 0.20 | 已安装 LICENSE.txt 为 GNU AGPL version 3 |

其余已登记依赖主要是 MIT、BSD、Apache-2.0、ISC、MPL-2.0 等；前端清单还包含 CC-BY-4.0。发布时仍须保留各自版权、许可及适用署名，不能因为主项目采用 AGPL 就删除依赖原声明。现有清单是包元数据核查，不是对所有文件及组合方式的法律担保。

建议限定 version 3，是为了清楚说明这次实际选择的版本；没有替作者预先授权未来版本。上游允许使用其他版本的部分，不会因此被剥夺其原本许可。

## 对作者和使用者的实际影响

- 可以免费或收费使用、修改和分发；AGPL 不是禁止商业使用的许可证。
- 分发受覆盖的修改版或衍生作品时，须按许可证提供相应源码，并保留许可、版权和修改说明。
- 将修改版通过网络提供给用户时，应按第13条向这些用户提供取得对应源码的机会。本版本提供指向构建时源码快照的下载入口，以及 GitHub 仓库链接。
- 不要求把学习者的上传资料、私人账号、API 密钥和业务数据库一起公开；相应源码与业务数据应分开。
- 不承诺主项目作者能给第三方依赖提供商业例外。若将来希望把整体改为宽松授权或闭源分发，需要先评估并替换相关依赖，或取得所需商业授权；不是简单把 LICENSE 换成 MIT。

## 实施与维护

1. 在根目录加入未改写的 AGPLv3 正文，并在 README 与包元数据明确 `AGPL-3.0-only`。
2. 补充自有代码的版权归属说明，不把第三方代码的版权写成自己的。
3. 保留第三方许可说明、版本清单与需要的署名；首轮公开以源码仓库为主，不顺带发布未经检查的镜像二进制包。
4. 如公开部署演示服务，在界面提供实际运行版本的源码入口；使用虚构数据且不配置付费密钥。
5. 本次最终 CI 通过后再次核对敏感文件，按已获授权转公开；之后仍需对新增资料与发布包逐次检查。

本许可决定只针对本仓库，不自动改变原源码仓库、生产服务或任何医院资料的授权。

## 原始来源

- [PyMuPDF 上游许可](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)
- [pymupdf4llm 上游许可](https://github.com/pymupdf/pymupdf4llm/blob/main/LICENSE)
- [EbookLib 上游许可](https://github.com/aerkalov/ebooklib/blob/master/LICENSE.txt)
- [AGPLv3 条款与概览](https://choosealicense.com/licenses/agpl-3.0/)
- [GNU AGPLv3 官方文本](https://www.gnu.org/licenses/agpl-3.0.html)
