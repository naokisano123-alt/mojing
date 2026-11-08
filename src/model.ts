export type Fate = 'off' | 'soft' | 'strong';
export type Page = 'cover' | 'home' | 'create' | 'story' | 'character' | 'atlas' | 'saves' | 'lore' | 'settings';
export interface Turn { id: string; action?: string; text: string[]; changes: string[]; time: number; fate: Fate }
export interface Snapshot { turns: Turn[]; money: number; energy: number; mind: number; time: number; location: string; fate: Fate }
export interface Save { id: string; title: string; branchName: string; snapshot: Snapshot }
export interface Branch extends Snapshot { id: string; name: string; parent?: string; logs: string[] }
export interface World { id: string; name: string; description: string; kind: string; documents: string[]; branches: Branch[]; active: string; saves: Save[] }
export interface State { version: 1; worlds: World[]; current: string; theme: 'light' | 'dark' | 'system'; style: 'literary' | 'concise'; mode: 'mixed' | 'novel'; fontSize?: 16 | 18 | 20; lineHeight?: 1.9 | 2.2 | 2.5 }
export const fateNames: Record<Fate,string> = { off: '关闭', soft: '温和', strong: '强力' };
export const uid = () => crypto.randomUUID();
export function clock(t: number) { return `第 ${Math.floor(t / 1440) + 1} 日 · ${String(Math.floor(t % 1440 / 60)).padStart(2,'0')}:${String(t % 60).padStart(2,'0')}`; }
export function snapshot(b: Branch): Snapshot { const {turns,money,energy,mind,time,location,fate}=b; return structuredClone({turns,money,energy,mind,time,location,fate}); }
export function makeWorld(name: string, description: string, documents: string[] = [], kind = 'custom'): World {
  const id=uid(), branchId=uid();
  return {id,name,description,documents,kind,active:branchId,saves:[],branches:[{id:branchId,name:'初始旅程',money:18,energy:82,mind:76,time:510,location:'雾港 · 旧街',fate:'off',logs:['在雾港开始了旅程'],turns:[{id:uid(),time:510,fate:'off',changes:[],text:['晨雾还没有散。沿着潮湿的石板路，你走进这座临海的小城。远处有船钟响起，声音穿过屋檐，落在尚未开门的店铺前。','旧街尽头，一家修补铺亮着灯。门边的木牌上写着：招一位帮手，按件计酬。柜台后的女人抬起头，把手中的铜扣轻轻放下。','“来避雨，还是来找点事情做？”她问。','你摸了摸口袋里的十八枚铜币。今天才刚刚开始。']}]}]};
}
export function initialState(): State { const w=makeWorld('雾港来信','在潮汐与旧日传闻之间，做一个没有注定结局的旅人。', ['雾港设定集.md'],'harbor'); const b=makeWorld('潮汐手记','同一座雾港，另一段旅途。从一封未署名的旧信开始。',[],'night'); return {version:1,worlds:[w,b],current:w.id,theme:'system',style:'literary',mode:'mixed'}; }
export function simulateTurn(b: Branch, action: string, concise = false): Branch {
 const n=structuredClone(b); let text:string[]=[]; const changes:string[]=[]; let elapsed=15;
 if(/休息|睡|歇/.test(action)){n.energy=Math.min(100,n.energy+15);n.mind=Math.min(100,n.mind+10);elapsed=30;text=['你在屋檐下找到一张长凳，坐下来听雨。街上的脚步来来往往，暂时没有谁需要你的回答。','片刻的停留让呼吸重新平稳。等你起身时，雾已经薄了一些。'];changes.push(`体力 +${n.energy-b.energy}`,`精神 +${n.mind-b.mind}`);}
 else if(/买|茶|面包/.test(action)){if(n.money>=3){n.money-=3; n.energy=Math.min(100,n.energy+5);text=['你向街边的摊主买了一杯热茶。三枚铜币落进木匣，升起的热气模糊了窗后的街景。','茶很淡，却足够让手指暖起来。'];changes.push('铜币 −3',`体力 +${n.energy-b.energy}`);}else{text=['你数了数剩下的铜币，还不够买一杯茶。摊主指了指街角的公共水井。'];changes.push('余额不足 · 未扣款');}}
 else if(/工作|帮手|铜扣|修补|交谈|询问/.test(action)){if(n.energy<8){text=['你试着集中精神，手指却有些发颤。店主将铜扣收回：“先歇一歇，手稳了再来。”'];changes.push('体力不足 · 先休息');}else{elapsed=40;n.energy-=8;n.money+=4; text=['你在修补铺坐下，仔细磨平一枚铜扣的边缘。店主没有催促，只偶尔伸手替你拨亮油灯。','她接过做好的铜扣，检查片刻，把四枚铜币推到你面前：“这件做得不错。”'];changes.push('铜币 +4','体力 −8');}}
 else if(/探索|走|码头|出门/.test(action)){elapsed=20;n.location=n.location.includes('码头')?'雾港 · 旧街':'雾港 · 码头';text=[`你沿着石阶走向${n.location.split(' · ')[1]}。风里多了一点盐和湿木头的味道。`,'路边的人各自忙着自己的事情。你放慢脚步，记住了回去的路。'];changes.push(`抵达 ${n.location.split(' · ')[1]}`);}
 else {text=['你把自己的打算记在旅途手记上，抬头望向渐渐明亮的街道。','这条自由行动尚未配置演示剧情。本轮只展示输入和回合反馈，不判定行动已经成功；你可以试试询问工作、探索码头、买茶或休息。'];changes.push('自由行动演示 · 未判定结果');elapsed=0;}
 if(elapsed && n.fate==='soft') text.push('临别时，一位路人多提醒了一句：午后旧街会贴出新的招工消息。也许值得再来看看。');
 if(elapsed && n.fate==='strong') text.push('正巧，修补铺的店主在街边叫住了你。她愿意介绍一位正在找帮手的熟客；是否赴约，仍由你决定。');
 n.time+=elapsed;changes.unshift(`耗时 ${elapsed} 分钟`);
 n.turns.push({id:uid(),action,text:concise?[text.join(' ').slice(0,240)]:text,changes,time:n.time,fate:n.fate});n.logs.push(`${clock(n.time)}｜${action}`);return n;
}
export function changeFate(b: Branch, fate: Fate): Branch {if(b.fate===fate)return b;return {...b,fate,logs:[...b.logs,`${clock(b.time)}｜主角光环：${fateNames[b.fate]} → ${fateNames[fate]}（下一回合）`]};}
export function forkSave(save: Save, parent: string): Branch {return {...structuredClone(save.snapshot),id:uid(),name:`${save.title} · 分支`,parent,logs:[`从「${save.title}」建立分支，原旅程保留`]};}
export function loadState(): State {try {const raw=JSON.parse(localStorage.getItem('mojing-m0-v1')||'null');if(raw?.version===1 && Array.isArray(raw.worlds) && raw.worlds.length && raw.worlds.every((w:World)=>Array.isArray(w.branches)&&w.branches.some(b=>b.id===w.active&&Array.isArray(b.turns)&&Array.isArray(b.logs))&&Array.isArray(w.saves)))return raw;}catch{/* unusable local data falls back to demo */}return initialState();}
