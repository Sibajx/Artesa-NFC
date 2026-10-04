import { createContext, useContext } from 'react';

// The roles of the signed-in account (ADR-030), from /me. Only for showing
// or hiding links: the API checks every role again on the server.
export const RolesContext = createContext<readonly string[]>(['editor']);

export const useRoles = (): readonly string[] => useContext(RolesContext);
