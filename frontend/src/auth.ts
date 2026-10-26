export type Session = {
  token: string
  expiresAt: number
  user: {
    id: number
    login: string
    role: 'CLIENT' | 'STAFF'
    full_name: string
  }
}

export async function signIn(
  login: string,
  password: string,
): Promise<Session> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login, password }),
  })

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Неверный логин или пароль.')
    }

    if (response.status === 429) {
      throw new Error('Слишком много попыток. Попробуйте через минуту.')
    }

    throw new Error('Не удалось выполнить вход. Попробуйте позже.')
  }

  const data = await response.json()

  if (
    typeof data.access_token !== 'string' ||
    typeof data.expires_in !== 'number'
  ) {
    throw new Error('Сервер вернул некорректный ответ.')
  }

  const userResponse = await fetch('/api/auth/me', {
    headers: {
      Authorization: `Bearer ${data.access_token}`,
    },
  })

  if (!userResponse.ok) {
    throw new Error('Не удалось загрузить профиль пользователя.')
  }

  const user = await userResponse.json()

  if (user.role !== 'CLIENT' && user.role !== 'STAFF') {
    throw new Error('Неизвестная роль пользователя.')
  }

  return {
    token: data.access_token,
    expiresAt: Date.now() + data.expires_in * 1000,
    user,
  }
}