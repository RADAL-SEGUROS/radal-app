/**
 * The shared vocabulary for a group's avatar (v8): the curated glyph set, the
 * initials fallback and the hashed-tone palette, used by both `GroupAvatar`
 * (render) and `IconPicker` (choose) so the two never drift.
 *
 * A group has NO logo of its own by default (José's note: no-logo → styled
 * name), so the fallback is never a broken image — it is the org initials in a
 * tone hashed deterministically off the name, using the semantic soft tokens so
 * it survives the dark toggle.
 */
import {
  Activity,
  Anchor,
  Award,
  Baby,
  BadgeCheck,
  Banknote,
  Barcode,
  Beef,
  Bike,
  Bird,
  Bolt,
  Bookmark,
  Boxes,
  Briefcase,
  BriefcaseMedical,
  Building,
  Building2,
  Bus,
  Car,
  CarFront,
  Caravan,
  Church,
  CircuitBoard,
  Coins,
  Cog,
  Compass,
  Construction,
  Container,
  Cpu,
  CreditCard,
  Cross,
  Database,
  DollarSign,
  Drill,
  Droplets,
  Factory,
  FileCheck,
  FileText,
  Fish,
  Flag,
  Flame,
  FlaskConical,
  Folder,
  Forklift,
  Gavel,
  Gem,
  Gift,
  Globe,
  GraduationCap,
  Grape,
  Hammer,
  HandCoins,
  Handshake,
  HardHat,
  Heart,
  HeartPulse,
  Home,
  Hotel,
  Landmark,
  Leaf,
  LifeBuoy,
  LineChart,
  Package,
  PencilRuler,
  Percent,
  PiggyBank,
  Pill,
  Plane,
  Power,
  Receipt,
  Ruler,
  Sailboat,
  Scale,
  Server,
  Settings,
  Shield,
  ShieldCheck,
  Ship,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Sprout,
  Star,
  Stethoscope,
  Store,
  Syringe,
  Tag,
  Tags,
  Tractor,
  TrainFront,
  TramFront,
  TrendingUp,
  TreePine,
  Trees,
  Truck,
  Umbrella,
  User,
  Users,
  Utensils,
  Vault,
  Wallet,
  Warehouse,
  Waves,
  Wheat,
  Wifi,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";

/**
 * The single source of truth: ordered categories a Chilean insurance broker
 * recognises. Each carries a stable `key` (its i18n label lives under
 * `group.icon.categories.<key>`, dynamic-key consumed in BOTH locales), a list
 * of emoji, and a list of glyph names.
 *
 * `glyphs` names are the stable `icon_value` stored server-side; every one MUST
 * resolve in `GROUP_GLYPHS` below, so the avatar renders it and never falls back
 * to initials by accident. The picker (choose) and the avatar (render) share
 * this file so the two can never drift.
 */
export type GroupIconCategory = {
  key: string;
  emojis: string[];
  glyphs: string[];
};

export const GROUP_ICON_CATEGORIES: GroupIconCategory[] = [
  {
    key: "property",
    emojis: ["🏢", "🏭", "🏬", "🏪", "🏠", "🏘️", "🏗️", "🏛️", "🏥", "🏨", "🏫", "🔥", "🚪", "🧱"],
    glyphs: ["building", "building2", "warehouse", "home", "factory", "store", "hotel", "church"],
  },
  {
    key: "vehicles",
    emojis: ["🚗", "🚙", "🚚", "🚛", "🚐", "🚌", "🏍️", "🚜", "🛵", "🚕", "🚑", "🚎"],
    glyphs: ["car", "car_front", "truck", "bus", "bike", "forklift", "caravan", "tram_front", "train_front", "plane"],
  },
  {
    key: "agro",
    emojis: ["🍇", "🌾", "🚜", "🌳", "🌲", "🌱", "🍎", "🐄", "🐑", "🐔", "🐟", "🍊", "🫒", "🌿"],
    glyphs: ["grape", "wheat", "tractor", "trees", "tree_pine", "sprout", "leaf", "beef", "bird", "fish"],
  },
  {
    key: "industry",
    emojis: ["⚙️", "🔧", "🔨", "🛠️", "🏗️", "⚡", "🔌", "🔥", "🧪", "⛽", "🏭", "📦"],
    glyphs: ["cog", "settings", "hammer", "drill", "construction", "zap", "power", "flame", "flask_conical", "boxes"],
  },
  {
    key: "health",
    emojis: ["❤️", "💗", "🫀", "🩺", "💊", "💉", "🧑", "👪", "👨‍👩‍👧", "🚑", "🩹", "➕"],
    glyphs: ["heart", "heart_pulse", "activity", "user", "users", "cross", "stethoscope", "pill", "syringe", "baby", "briefcase_medical"],
  },
  {
    key: "liability",
    emojis: ["⚖️", "🛡️", "📜", "📄", "📋", "🤝", "✅", "🏛️"],
    glyphs: ["scale", "shield", "shield_check", "gavel", "file_text", "file_check", "handshake", "badge_check"],
  },
  {
    key: "commerce",
    emojis: ["🏪", "🛒", "🛍️", "📦", "🏷️", "🧾", "💳", "🎁", "🏬"],
    glyphs: ["shopping_cart", "shopping_bag", "package", "tag", "tags", "receipt", "credit_card", "barcode", "gift"],
  },
  {
    key: "finance",
    emojis: ["🏦", "💰", "🪙", "💵", "📈", "📊", "👛", "💳", "🐷", "💹"],
    glyphs: ["landmark", "coins", "dollar_sign", "line_chart", "trending_up", "wallet", "piggy_bank", "banknote", "vault", "hand_coins", "percent"],
  },
  {
    key: "engineering",
    emojis: ["👷", "🦺", "🔧", "🛠️", "🖥️", "💻", "🔌", "📡", "🗄️", "⚙️"],
    glyphs: ["hard_hat", "wrench", "server", "cpu", "circuit_board", "ruler", "pencil_ruler", "wifi", "database", "bolt"],
  },
  {
    key: "marine",
    emojis: ["⚓", "🚢", "⛴️", "🛳️", "⛵", "🌊", "🐟", "🧭", "🛟", "📦"],
    glyphs: ["anchor", "ship", "sailboat", "container", "waves", "life_buoy", "compass", "droplets"],
  },
  {
    key: "general",
    emojis: ["⭐", "🚩", "📁", "💼", "🌐", "🛡️", "💎", "🎓", "🍽️", "☂️", "🏅", "✨", "🔖", "🏢"],
    glyphs: ["star", "flag", "folder", "briefcase", "globe", "gem", "graduation_cap", "utensils", "umbrella", "award", "sparkles", "bookmark"],
  },
];

/**
 * The curated glyph registry: stable `icon_value` name → lucide component.
 * EVERY name referenced by a category's `glyphs` above appears here, so a picked
 * glyph always renders. Older stored names are preserved for backward-compat.
 */
export const GROUP_GLYPHS: Record<string, LucideIcon> = {
  // property
  building: Building,
  building2: Building2,
  warehouse: Warehouse,
  home: Home,
  factory: Factory,
  store: Store,
  hotel: Hotel,
  church: Church,
  // vehicles
  car: Car,
  car_front: CarFront,
  truck: Truck,
  bus: Bus,
  bike: Bike,
  forklift: Forklift,
  caravan: Caravan,
  tram_front: TramFront,
  train_front: TrainFront,
  plane: Plane,
  // agro
  grape: Grape,
  wheat: Wheat,
  tractor: Tractor,
  trees: Trees,
  tree_pine: TreePine,
  sprout: Sprout,
  leaf: Leaf,
  beef: Beef,
  bird: Bird,
  fish: Fish,
  // industry
  cog: Cog,
  settings: Settings,
  hammer: Hammer,
  drill: Drill,
  construction: Construction,
  zap: Zap,
  power: Power,
  flame: Flame,
  flask_conical: FlaskConical,
  boxes: Boxes,
  // health
  heart: Heart,
  heart_pulse: HeartPulse,
  activity: Activity,
  user: User,
  users: Users,
  cross: Cross,
  stethoscope: Stethoscope,
  pill: Pill,
  syringe: Syringe,
  baby: Baby,
  briefcase_medical: BriefcaseMedical,
  // liability
  scale: Scale,
  shield: Shield,
  shield_check: ShieldCheck,
  gavel: Gavel,
  file_text: FileText,
  file_check: FileCheck,
  handshake: Handshake,
  badge_check: BadgeCheck,
  // commerce
  shopping_cart: ShoppingCart,
  shopping_bag: ShoppingBag,
  package: Package,
  tag: Tag,
  tags: Tags,
  receipt: Receipt,
  credit_card: CreditCard,
  barcode: Barcode,
  gift: Gift,
  // finance
  landmark: Landmark,
  coins: Coins,
  dollar_sign: DollarSign,
  line_chart: LineChart,
  trending_up: TrendingUp,
  wallet: Wallet,
  piggy_bank: PiggyBank,
  banknote: Banknote,
  vault: Vault,
  hand_coins: HandCoins,
  percent: Percent,
  // engineering
  hard_hat: HardHat,
  wrench: Wrench,
  server: Server,
  cpu: Cpu,
  circuit_board: CircuitBoard,
  ruler: Ruler,
  pencil_ruler: PencilRuler,
  wifi: Wifi,
  database: Database,
  bolt: Bolt,
  // marine
  anchor: Anchor,
  ship: Ship,
  sailboat: Sailboat,
  container: Container,
  waves: Waves,
  life_buoy: LifeBuoy,
  compass: Compass,
  droplets: Droplets,
  // general
  star: Star,
  flag: Flag,
  folder: Folder,
  briefcase: Briefcase,
  globe: Globe,
  gem: Gem,
  graduation_cap: GraduationCap,
  utensils: Utensils,
  umbrella: Umbrella,
  award: Award,
  sparkles: Sparkles,
  bookmark: Bookmark,
};

export const GROUP_GLYPH_NAMES = Object.keys(GROUP_GLYPHS);

/** Flat emoji list (deduped, category order) — kept for any legacy consumer. */
export const GROUP_EMOJIS = Array.from(
  new Set(GROUP_ICON_CATEGORIES.flatMap((category) => category.emojis)),
);

/** Deterministic tone bucket off the name — 4 semantic soft/strong pairs. */
const PALETTE: { bg: string; fg: string }[] = [
  { bg: "var(--brand-soft)", fg: "var(--brand-deep)" },
  { bg: "var(--warn-soft)", fg: "var(--warn-text)" },
  { bg: "var(--pos-soft)", fg: "var(--pos-text)" },
  { bg: "var(--neg-soft)", fg: "var(--neg-text)" },
];

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function hashedTone(name: string): { bg: string; fg: string } {
  return PALETTE[hashString(name || "?") % PALETTE.length];
}

/** Up to two initials from the group name (letters/digits only). */
export function initials(name: string): string {
  const words = (name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) {
    const clean = words[0].replace(/[^\p{L}\p{N}]/gu, "");
    return (clean.slice(0, 2) || "?").toUpperCase();
  }
  const first = words[0].replace(/[^\p{L}\p{N}]/gu, "").charAt(0);
  const second = words[words.length - 1].replace(/[^\p{L}\p{N}]/gu, "").charAt(0);
  return ((first + second) || "?").toUpperCase();
}

export function GlyphIcon({ name, className }: { name: string; className?: string }) {
  const Icon = GROUP_GLYPHS[name] ?? Building2;
  return <Icon className={className} aria-hidden />;
}
