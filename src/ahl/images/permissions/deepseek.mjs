// Registered at host scope so native dispatch enforces this across all agents.
export const name = "ahl-native-permissions";
export const inject = ["tools"];

export function apply(ctx, config) {
  const denied = new Set(config.deny);
  ctx.tools.guard(({ name }) =>
    denied.has(name) ? `Denied by AHL permissions: ${name}` : undefined
  );
}
