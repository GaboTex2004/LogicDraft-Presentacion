import type { EntityAttribute } from '../types/diagram.types.ts'

/** Manual edits and AI PK operations preserve the same non-nullable key invariant. */
export function updateEntityAttribute(
  attribute: EntityAttribute,
  changes: Partial<Omit<EntityAttribute, 'id'>>,
): EntityAttribute {
  const updated = { ...attribute, ...changes }
  if (updated.primaryKey) updated.nullable = false
  return updated
}
