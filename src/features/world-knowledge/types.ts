/**
 * M3.3 人物知识、信息传播与玩家视角前端类型契约
 * 严格对应 docs/contracts/M3_WAVE1_API.md §4 与 M3_WAVE1_GEMINI_MA.md
 */

export type KnowledgeType = 'confirmed' | 'suspected' | 'rumor' | 'mistaken';

export type KnowledgeChannel = 'observed' | 'notice' | 'conversation' | 'rumor';

export type KnowledgeStatus = 'active' | 'superseded' | 'retracted';

/**
 * 玩家视角公开知情条目（与叙事上下文使用同一投影语义）
 * 严禁包含未公开的真实权威 event_id、隐藏事件标题、事件总数或未来更新时间
 */
export interface WorldNewsItem {
  fact_id: string;
  holder_id?: string;
  knowledge_type: KnowledgeType;
  title: string;
  content: string;
  channel: KnowledgeChannel;
  source_person_id?: string | null;
  source_label: string;
  learned_minute: number;
  confidence: number;
  status?: KnowledgeStatus;
  revision: number;
}

/**
 * GET /api/v1/adventures/{id}/world-news 分页响应
 * next_before 为不透明分页游标，不暴露隐藏事件数或全局序号
 */
export interface WorldNewsPage {
  items: WorldNewsItem[];
  next_before: string | null;
}

export interface ListWorldNewsOptions {
  before?: string | null;
  limit?: number;
  signal?: AbortSignal;
}

/**
 * 可注入的消息读取传输层接口（支持真实 HTTP 与独立 Fake 验收）
 */
export interface WorldKnowledgeTransport {
  listWorldNews(
    adventureId: string,
    options?: ListWorldNewsOptions,
  ): Promise<WorldNewsPage>;
}
