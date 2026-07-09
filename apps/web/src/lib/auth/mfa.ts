/**
 * MFA sign-in signal constants, in a server-free leaf module so both the
 * NextAuth authorize() (server) and the SignInForm (client) can import them
 * without dragging server-only dependencies into the client bundle.
 *
 * These are the `result.error` values authorize() throws to drive the two-step
 * TOTP flow in the sign-in form.
 */

export const MFA_REQUIRED_ERROR = "MFA_REQUIRED";
export const MFA_INVALID_CODE_ERROR = "MFA_INVALID_CODE";
