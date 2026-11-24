        import {workspace} from '../workspace.js';
        import {plotBoard} from './plot-board.js';

        const page = {
  "title": "灯影分区平面",
  "module": "遮挡图谱",
  "caption": "遮挡图谱",
  "detail": "沿网格邻接关系建图。相邻格的遮挡与串光越接近，谱聚类越倾向于放入同一组。",
  "path": "/shadow_plans",
  "columns": [
    {
      "name": "code",
      "label": "编号"
    },
    {
      "name": "band",
      "label": "投影带"
    },
    {
      "name": "revision",
      "label": "底图版本"
    },
    {
      "name": "clusters",
      "label": "簇数"
    },
    {
      "name": "layout",
      "label": "簇及网格坐标"
    },
    {
      "name": "evidence",
      "label": "谱聚类统计"
    },
    {
      "name": "token",
      "label": "任务凭证"
    }
  ],
  "layout": "split",
  "view": "table",
  "actions": [
    {
      "name": "segment_bank",
      "label": "测算灯影分区",
      "path": "/shadow_plans/segment-bank",
      "fields": [
        {
          "name": "band",
          "label": "投影带",
          "kind": "text",
          "initial": "BANK-01"
        },
        {
          "name": "clusters",
          "label": "划分灯组数",
          "kind": "int",
          "initial": 3
        }
      ],
      "help": "运行当前底图，记录版本、组号与邻边切分。",
      "roles": [
        "operator",
        "reviewer"
      ],
      "success": "已写入"
    },
    {
      "name": "pin_map",
      "label": "归档分区平面",
      "path": "/shadow_plans/pin-map",
      "fields": [
        {
          "name": "code",
          "label": "图谱号",
          "kind": "text",
          "initial": "MAP-BANK-01"
        },
        {
          "name": "token",
          "label": "计算任务凭证",
          "kind": "token",
          "initial": ""
        }
      ],
      "help": "保存灯组分区，供观测调岗时定位。",
      "roles": [
        "reviewer"
      ],
      "success": "已写入"
    },
    {
      "name": "trial_closures",
      "label": "提交封路试算（编号与 JSON 表单）",
      "path": "/route_sketches/trial-block",
      "fields": [
        { "name": "code", "label": "草图号", "kind": "text", "initial": "SKETCH-BANK-01" },
        { "name": "plan", "label": "归档原图号", "kind": "text", "initial": "MAP-BANK-01" },
        { "name": "closures", "label": "封路边编号对（JSON）", "kind": "json", "initial": [] }
      ],
      "help": "只封相邻方格间的通路；同一灯组被片区割裂时测算失败并指出断开的格子。",
      "roles": ["operator", "reviewer"],
      "success": "已试算"
    },
    {
      "name": "restore_sketch",
      "label": "恢复最近可用草图（编号表单）",
      "path": "/route_sketches/restore-sketch",
      "fields": [
        { "name": "code", "label": "草图号", "kind": "text", "initial": "SKETCH-BANK-01" }
      ],
      "help": "测算失败后恢复最近一张可用草图及其封路。",
      "roles": ["operator", "reviewer"],
      "success": "已恢复"
    },
    {
      "name": "archive_sketch",
      "label": "归档封路草图（编号表单）",
      "path": "/route_sketches/archive-sketch",
      "fields": [
        { "name": "code", "label": "草图号", "kind": "text", "initial": "SKETCH-BANK-01" }
      ],
      "help": "草图必须跟得上最新封路；归档后复核员可在图谱页面查询原图与封路。",
      "roles": ["operator", "reviewer"],
      "success": "已归档"
    }
  ]
};

        export const {title} = page;
        export const key = "shadow_plans";
        export async function render(root, role) {
          await workspace(root, role, {...page, renderData: rows => plotBoard(rows, role)});

        }
