// Authentication types based on backend implementation

export interface Organization {
  id: number;
  name: string;
  slug: string;
  plan_id?: number | null;
}

export interface User {
  id?: string | number;
  email: string;
  username: string;
  first_name?: string;
  last_name?: string;
  avatar?: string;
  is_staff?: boolean;
  is_org_admin?: boolean;
  is_csm_admin?: boolean;
  organization: Organization | null;
  current_organization: Organization | null;
  roles: string[];
  team_id?: number;
  job?: string;
  department?: string;
  location?: string;
  password_rotation?: PasswordRotationStatus;
}

export interface PasswordRotationStatus {
  required: boolean;
  warning: boolean;
  elevated: boolean;
  expires_at: string | null;
  days_until_expiry: number | null;
  max_age_days: number;
  warning_days: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  refresh: string;
  user: User;
  message: string;
  organization_access_token?: string;
  requires_password_setup?: boolean;
}

export interface ChangePasswordResponse {
  message: string;
  user: User;
  password_rotation: PasswordRotationStatus;
}

export interface RegisterRequest {
  email: string;
  password: string;
  username: string;
  organization_id?: number;
  role?: string;
}

export interface RegisterResponse {
  message: string;
  token: string;
  refresh: string;
  user: User;
  organization_access_token?: string;
}

export interface PasswordValidationRule {
  id: string;
  help_text: string;
  valid: boolean | null;
  errors: string[];
}

// Google OAuth types
export interface GoogleAuthResponse {
  message: string;
  token?: string;
  refresh?: string;
  user?: User;
  requires_password_setup?: boolean;
  temp_token?: string;
  redirect_url?: string;
  organization_access_token?: string;
}

export interface SetPasswordRequest {
  token: string;
  password: string;
}

export interface AuthError {
  error: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  loading: boolean;
}

// Form validation types
export interface FormValidation {
  email?: string;
  password?: string;
  username?: string;
  confirmPassword?: string;
  organization_id?: string;
  general?: string;
}

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  fieldErrors?: FormValidation;
  statusCode?: number;
  errorCode?: string;
  retry_after_seconds?: number;
  requires_captcha?: boolean;
  lockout_until?: string;
}

export interface SsoRedirectParams {
  org: string;
}

export interface SsoCallbackParams {
  code: string;
  state?: string;
}
