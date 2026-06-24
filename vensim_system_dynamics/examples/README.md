# 示例模型

`population_demo.mdl` 是最小人口库存—流率模型：库存 `Population`，流入 `Births`，流出 `Deaths`，辅助变量 `Birth Fraction`、`Average Lifetime`。

先运行：

```bash
python ../tools/vensim_autolayout.py inspect population_demo.mdl
python ../tools/vensim_autolayout.py audit population_demo.mdl
```

`inspect` 输出会标注每个对象是 `var/valve/src/sink`，是否 `attached`(附着阀门)、`shadow`(影子变量)、`stock_like`(库存状)；每条箭头标注 `FLOW`(物理流率管道) 或 `info`(信息箭头)。

SFD 自动排版时，在 `layout_config_sfd.json` 的 `lock_node_names` 中锁定 `Population`、`Births`、`Deaths`，使库存与流率标签不动；`Birth Fraction`、`Average Lifetime` 作为普通辅助变量由 Graphviz 整理，其信息箭头会被设为单控制点圆弧。物理流率管道(阀门↔库存↔云，weight=22)保持原样。
