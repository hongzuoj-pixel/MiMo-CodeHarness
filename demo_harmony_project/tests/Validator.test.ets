import { validateUsername, validatePassword, validatePhone, normalizeInput } from '../utils/Validator'
import { login, bindPhone } from '../pages/LoginPage'
import { loadHomeData } from '../pages/HomePage'

export function testValidatorRules(): boolean {
  return !validateUsername('  ')
    && !validateUsername('ab')
    && validateUsername('ailin')
    && !validatePassword('123')
    && validatePassword('123456')
    && validatePhone('13800000000')
    && !validatePhone('123')
    && normalizeInput('  hi  ') === 'hi'
}

export function testPageFlow(): boolean {
  return login('ailin', '123456').length > 0
    && bindPhone('13800000000').length > 0
    && loadHomeData('u001').length > 0
}
