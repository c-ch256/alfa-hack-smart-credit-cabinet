import { useState } from 'react'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import { InputDesktop } from '@alfalab/core-components/input/desktop'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './App.css'
import type { FormEvent } from 'react'
import { signIn } from './auth'
import type { Session } from './auth'

type LoginPageProps = {
  onLogin: (session: Session) => void
}

function LoginPage({ onLogin }: LoginPageProps) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (loading) return

    setError('')

    if (!login.trim() || !password) {
      setError('Введите логин и пароль.')
      return
    }

    setLoading(true)

    try {
      const session = await signIn(login.trim(), password)
      setPassword('')
      onLogin(session)
    } catch (cause) {
      setError(
        cause instanceof TypeError
          ? 'Нет соединения с сервером. Попробуйте ещё раз.'
          : cause instanceof Error
            ? cause.message
            : 'Не удалось выполнить вход.',
      )
    } finally {
      setLoading(false)
    }
  }
  return (
    <div className="login-page">
      <header className="login-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            А
          </span>
          <span>Кредитный кабинет</span>
        </div>

        <span className="demo-badge">Демо-режим</span>
      </header>

      <main className="login-main">
        <section className="login-card" aria-labelledby="login-title">
          <div className="login-heading">
            <h1 id="login-title">Вход в кабинет</h1>
            <p>Кредитный кабинет предпринимателя</p>
          </div>

          <form className="login-fields" onSubmit={handleSubmit}>
            <div className="login-field">
              <label htmlFor="login">Логин</label>

              <InputDesktop
                id="login"
                name="login"
                block
                size={48}
                colors="inverted"
                placeholder="Введите логин"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={login}
                onChange={(_, { value }) => setLogin(value)}
              />
            </div>

            <div className="login-field">
              <label htmlFor="password">Пароль</label>

              <InputDesktop
                id="password"
                name="password"
                block
                size={48}
                colors="inverted"
                type={passwordVisible ? 'text' : 'password'}
                placeholder="Введите пароль"
                autoComplete="current-password"
                value={password}
                onChange={(_, { value }) => setPassword(value)}
                rightAddons={
                  <button
                    className="password-toggle"
                    type="button"
                    aria-label={
                      passwordVisible ? 'Скрыть пароль' : 'Показать пароль'
                    }
                    aria-pressed={passwordVisible}
                    onClick={() => setPasswordVisible(!passwordVisible)}
                  >
                    {passwordVisible ? 'Скрыть' : 'Показать'}
                  </button>
                }
              />
            </div>

            {error && (
  <p role="alert" style={{ color: '#ff8a80', margin: 0, fontSize: 13 }}>
    {error}
  </p>
)}

<ButtonDesktop
  className="login-submit"
  view="accent"
  size={48}
  block
  type="submit"
  loading={loading}
  disabled={loading}
>
  Войти
</ButtonDesktop>
          </form>

          <aside className="demo-accounts" aria-label="Демонстрационные аккаунты">
            <h2>
              <span className="demo-dot" />
              Демо-аккаунты
            </h2>

            <div className="account-row">
              <span>Предприниматель</span>
              <strong>client1 — client4</strong>
            </div>

            <div className="account-row">
              <span>Сотрудник банка</span>
              <strong>staff1</strong>
            </div>

            <p>Данные для входа предоставляет команда проекта.</p>
          </aside>
        </section>
      </main>

      <footer className="login-footer">
        Демонстрационный кредитный кабинет · Синтетические данные
      </footer>
    </div>
  )
}

export default LoginPage