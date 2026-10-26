import { randomInt } from '@sim/utils/random'

// The pinned native generator uses this same alphabet and CSPRNG without server encryption.
export function generatePassword(length = 24) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_-+='
  let result = ''
  for (let i = 0; i < length; i++) result += chars.charAt(randomInt(0, chars.length))
  return result
}
