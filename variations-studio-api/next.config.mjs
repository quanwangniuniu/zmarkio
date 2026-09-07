/** @type {import('next').NextConfig} */
const nextConfig = {
  // Django API uses trailing slashes; do not 308 away Authorization headers.
  skipTrailingSlashRedirect: true,
};

export default nextConfig;
