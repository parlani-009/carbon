import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import './App.css'

const API_BASE = 'http://localhost:8000/api'

function App() {
  const [chats, setChats] = useState([])
  const [activeChatId, setActiveChatId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const messagesEndRef = useRef(null)
  const messagesContainerRef = useRef(null)

  // Load chat list on mount
  useEffect(() => {
    fetchChats()
  }, [])

  // Poll for new messages when a chat is active
  useEffect(() => {
    if (!activeChatId) return
    fetchMessages(activeChatId)
    const interval = setInterval(() => fetchMessages(activeChatId), 2000)
    return () => clearInterval(interval)
  }, [activeChatId])

  // Smart scroll: only auto-scroll if user is already at the bottom
  useEffect(() => {
    if (isAtBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isAtBottom])

  function handleScroll() {
    const container = messagesContainerRef.current
    if (!container) return
    const { scrollTop, scrollHeight, clientHeight } = container
    const nearBottom = scrollHeight - scrollTop - clientHeight < 80
    setIsAtBottom(nearBottom)
  }

  async function fetchChats() {
    try {
      const res = await fetch(`${API_BASE}/chat/list/`)
      const data = await res.json()
      setChats(data.chats || [])
    } catch (err) {
      console.error('Failed to load chats:', err)
    }
  }

  async function fetchMessages(chatId) {
    try {
      const res = await fetch(`${API_BASE}/chat/messages/?chat_id=${chatId}`)
      const data = await res.json()
      if (data.messages) setMessages(data.messages)
    } catch (err) {
      console.error('Failed to load messages:', err)
    }
  }

  async function handleMakeChat() {
    try {
      const res = await fetch(`${API_BASE}/chat/make/`, { method: 'POST' })
      const data = await res.json()
      setActiveChatId(data.chat_id)
      setMessages([])
      fetchChats()
    } catch (err) {
      console.error('Failed to create chat:', err)
    }
  }

  async function handleSendMessage(e) {
    e.preventDefault()
    if (!input.trim() || !activeChatId || isLoading) return

    const query = input.trim()
    setInput('')
    setIsLoading(true)

    try {
      await fetch(`${API_BASE}/chat/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: activeChatId, query })
      })
      // Messages will be fetched via polling
    } catch (err) {
      console.error('Failed to send message:', err)
    } finally {
      setIsLoading(false)
    }
  }

  function formatMessageContent(content) {
    if (!content) return ''
    if (typeof content === 'string') return content
    if (content.text) return content.text
    return JSON.stringify(content, null, 2)
  }

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>CarBot</h2>
          <button className="home-btn" onClick={handleMakeChat} title="New Chat">
            +
          </button>
        </div>
        <div className="chat-list">
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={`chat-item ${activeChatId === chat.id ? 'active' : ''}`}
              onClick={() => setActiveChatId(chat.id)}
            >
              {chat.title}
            </div>
          ))}
        </div>
      </aside>

      {/* Chat Area */}
      <main className="chat-area">
        {!activeChatId ? (
          <div className="chat-empty">
            <p>Select a chat or start a new one</p>
            <button className="primary-btn" onClick={handleMakeChat}>
              Start New Chat
            </button>
          </div>
        ) : (
          <>
            <div className="messages-container" onScroll={handleScroll} ref={messagesContainerRef}>
              {messages.length === 0 && (
                <div className="messages-empty">
                  Send a message to start the conversation
                </div>
              )}
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`message message-${msg.sender}`}
                >
                  <div className="message-sender">
                    {msg.sender === 'user' ? 'You' : 'CarBot'}
                  </div>
                  <div className="message-content">
                    <ReactMarkdown>{formatMessageContent(msg.content)}</ReactMarkdown>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
            <form className="chat-input-area" onSubmit={handleSendMessage}>
              <input
                className="chat-input"
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about cars..."
                disabled={isLoading}
              />
              <button
                type="submit"
                className="send-btn"
                disabled={!input.trim() || isLoading}
              >
                Send
              </button>
            </form>
          </>
        )}
      </main>
    </div>
  )
}

export default App
