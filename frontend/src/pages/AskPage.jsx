import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  History,
  LockKeyhole,
  MessageCircle,
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  Trash2,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { playUiSound, setSoundsEnabled, soundsEnabled } from '../utils/sounds';

const STARTER_QUESTIONS = [
  'Can I afford a R25,000 phone?',
  'What can I do to save for a car in 2 years?',
  'Where can I realistically reduce my spending?',
  'Why did I spend more last month?',
];

const STATUS_LABELS = {
  comfortably_affordable: 'Comfortably affordable',
  affordable_with_planning: 'Affordable with planning',
  possible_but_straining: 'Would strain your finances',
  not_currently_realistic: 'Not currently realistic',
  insufficient_information: 'More information needed',
  on_track: 'On track',
  close_to_target: 'Close to target',
  adjustment_needed: 'Adjustment needed',
  off_track: 'Off track',
  improving: 'Improving',
  stable: 'Stable',
  spending_increased: 'Spending increased',
  mixed: 'Mixed picture',
  within_budget: 'Within budget',
  slightly_over: 'Slightly over budget',
  over_budget: 'Over budget',
  budget_needs_review: 'Budget needs review',
  positive: 'Positive',
  neutral: 'Overview',
  attention: 'Needs attention',
};


function formatChatDate(value) {
  if (!value) return '';
  const date = new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z');
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

function PrivacyDetails({ dataUsed }) {
  const [open, setOpen] = useState(false);
  if (!dataUsed) return null;

  return (
    <div className="ai-data-used">
      <button type="button" onClick={() => setOpen((value) => !value)}>
        <LockKeyhole size={15} />
        Data used for this answer
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && (
        <div className="ai-data-used-panel">
          {dataUsed.traceId && <div><strong>{dataUsed.traceId}</strong><span>terminal trace ID</span></div>}
          {dataUsed.answerMode === 'local' ? (
            <>
              <div><strong>Local</strong><span>answer mode</span></div>
              <div><strong>{dataUsed.classifierUsed ? 'Yes' : 'No'}</strong><span>tiny AI router used</span></div>
              <div><strong>{dataUsed.classifierTotalTokens || 0}</strong><span>routing tokens used</span></div>
              <div><strong>0</strong><span>AI answer-generation tokens</span></div>
              <div><strong>No</strong><span>statement financial data sent externally</span></div>
              <div><strong>No</strong><span>statement PDF/raw bank descriptions sent</span></div>
              <div><strong>WalletWise</strong><span>calculated the answer locally</span></div>
            </>
          ) : (
            <>
              <div><strong>AI-assisted</strong><span>answer mode</span></div>
              <div><strong>{dataUsed.classifierUsed ? 'Yes' : 'No'}</strong><span>tiny AI router used</span></div>
              <div><strong>{dataUsed.classifierTotalTokens || 0}</strong><span>routing tokens used</span></div>
              <div><strong>{dataUsed.answerTotalTokens || 0}</strong><span>answer tokens used</span></div>
              <div><strong>{dataUsed.monthsIncluded || 0}</strong><span>recent periods summarised</span></div>
              <div><strong>{dataUsed.transactionDetailsIncluded || 0}</strong><span>sanitised transactions included</span></div>
              <div><strong>{dataUsed.savedConversationMessagesSent || 0}</strong><span>recent chat messages sent to AI</span></div>
              <div><strong>{dataUsed.conversationSummarySent ? 'Yes' : 'No'}</strong><span>compact chat summary sent to AI</span></div>
              <div><strong>No</strong><span>statement PDF sent</span></div>
              <div><strong>No</strong><span>raw descriptions or account/card numbers sent</span></div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SentimentPill({ sentiment = 'neutral', children }) {
  return <span className={`ai-sentiment ai-sentiment-${sentiment}`}>{children}</span>;
}

function StructuredAnswer({ answer, onUseFollowUp }) {
  if (!answer || typeof answer !== 'object') return null;
  const metrics = Array.isArray(answer.key_metrics) ? answer.key_metrics : [];
  const insights = Array.isArray(answer.insights) ? answer.insights : [];
  const recommendations = Array.isArray(answer.recommendations) ? answer.recommendations : [];
  const warnings = Array.isArray(answer.warnings) ? answer.warnings : [];

  return (
    <div className="ai-answer-card">
      <div className="ai-answer-head">
        <div>
          <span className={`ai-status ai-status-${answer.status || 'neutral'}`}>
            {STATUS_LABELS[answer.status] || String(answer.status || 'Wally')}
          </span>
          <h2>{answer.title}</h2>
          <p>{answer.summary}</p>
        </div>
      </div>

      {metrics.length > 0 && (
        <div className="ai-metric-grid">
          {metrics.map((metric, index) => (
            <div className={`ai-metric-card ai-tone-${metric.sentiment || 'neutral'}`} key={`${metric.label}-${index}`}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              {metric.change && <small>{metric.change}</small>}
            </div>
          ))}
        </div>
      )}

      {insights.length > 0 && (
        <section className="ai-answer-section">
          <h3>What this means</h3>
          <div className="ai-insight-list">
            {insights.map((insight, index) => (
              <article key={`${insight.title}-${index}`}>
                <div className="ai-insight-title-row">
                  <strong>{insight.title}</strong>
                  <SentimentPill sentiment={insight.sentiment}>{insight.sentiment}</SentimentPill>
                </div>
                <p>{insight.text}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      {recommendations.length > 0 && (
        <section className="ai-answer-section">
          <h3>What you can do</h3>
          <div className="ai-recommendation-list">
            {recommendations.map((item, index) => (
              <article key={`${item.title}-${index}`}>
                <span className="ai-step-number">{index + 1}</span>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.text}</p>
                  {item.impact && <small>{item.impact}</small>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {warnings.length > 0 && (
        <section className="ai-warning-box">
          <strong>Keep in mind</strong>
          <ul>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
        </section>
      )}

      {answer.next_step && (
        <section className="ai-next-step">
          <span>Next step</span>
          <strong>{answer.next_step}</strong>
        </section>
      )}

      {answer.needs_user_input && answer.follow_up_question && (
        <button type="button" className="ai-follow-up-button" onClick={() => onUseFollowUp?.(answer.follow_up_question)}>
          {answer.follow_up_question}
        </button>
      )}
    </div>
  );
}

export default function AskPage({ bankScope = 'all' }) {
  const [status, setStatus] = useState(null);
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [activeChatTitle, setActiveChatTitle] = useState('New chat');
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem('walletwise_wally_history_collapsed') === 'true';
    } catch {
      return false;
    }
  });
  const [soundOn, setSoundOn] = useState(() => soundsEnabled());
  const [error, setError] = useState('');
  const bottomRef = useRef(null);

  const refreshChats = async () => {
    try {
      const response = await api.getAssistantChats();
      setChats(response || []);
    } catch {
      // Chat history is helpful but should not block the assistant itself.
    }
  };

  useEffect(() => {
    Promise.all([api.getAssistantStatus(), api.getAssistantChats()])
      .then(([assistantStatus, savedChats]) => {
        setStatus(assistantStatus);
        setChats(savedChats || []);
      })
      .catch((err) => setError(err.message || 'Could not connect to Wally.'));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, sending]);

  const collapseHistory = () => {
    setHistoryCollapsed(true);
    try {
      window.localStorage.setItem('walletwise_wally_history_collapsed', 'true');
    } catch {
      // Local preference only.
    }
  };

  const expandHistory = () => {
    if (window.matchMedia('(max-width: 900px)').matches) {
      setHistoryOpen(true);
      return;
    }
    setHistoryCollapsed(false);
    try {
      window.localStorage.setItem('walletwise_wally_history_collapsed', 'false');
    } catch {
      // Local preference only.
    }
  };

  const startNewChat = () => {
    playUiSound('new');
    setActiveChatId(null);
    setActiveChatTitle('New chat');
    setMessages([]);
    setQuestion('');
    setError('');
    setHistoryOpen(false);
  };

  const openChat = async (chat) => {
    if (!chat?.id || chat.id === activeChatId || loadingChat) return;
    playUiSound('navigate');
    setLoadingChat(true);
    setError('');
    try {
      const saved = await api.getAssistantChat(chat.id);
      setActiveChatId(saved.id);
      setActiveChatTitle(saved.title || 'Saved chat');
      setMessages(saved.messages || []);
      setHistoryOpen(false);
    } catch (err) {
      setError(err.message || 'Could not open that saved chat.');
    } finally {
      setLoadingChat(false);
    }
  };

  const deleteChat = async (event, chat) => {
    event.stopPropagation();
    if (!chat?.id) return;
    try {
      await api.deleteAssistantChat(chat.id);
      playUiSound('delete');
      if (chat.id === activeChatId) startNewChat();
      await refreshChats();
    } catch (err) {
      setError(err.message || 'Could not delete that chat.');
    }
  };

  const toggleSounds = () => {
    const next = !soundOn;
    setSoundOn(next);
    setSoundsEnabled(next);
    if (next) playUiSound('receive');
  };

  const submit = async (value = question) => {
    const clean = String(value || '').trim();
    if (!clean || sending) return;

    playUiSound('send');
    const userMessage = { role: 'user', content: clean };
    setMessages((current) => [...current, userMessage]);
    setQuestion('');
    setError('');
    setSending(true);

    try {
      const response = await api.askAssistant({
        question: clean,
        conversationId: activeChatId,
      });
      setActiveChatId(response.conversationId || activeChatId);
      setActiveChatTitle(response.conversationTitle || activeChatTitle);
      setMessages((current) => [...current, {
        role: 'assistant',
        answer: response.answer,
        dataUsed: response.dataUsed,
        model: response.model,
      }]);
      playUiSound('receive');
      await refreshChats();
    } catch (err) {
      setError(err.message || 'Wally could not answer that question.');
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const useFollowUp = (value) => {
    setQuestion(value || '');
    requestAnimationFrame(() => document.querySelector('.ai-composer textarea')?.focus());
  };

  return (
    <div className="page ai-page wally-page">
      <div className="page-heading desktop-page-title">
        <p>Wally</p>
        <h1>Your WalletWise money companion</h1>
        <span>Ask about affordability, spending, savings or goals. Wally only receives the financial context needed for your question.</span>
      </div>

      <div className="wally-bank-scope-note">
        Wally is using <strong>{bankScope === 'all' ? 'all imported banks' : `${bankScope.split(',').join(' and ')} transactions only`}</strong> for new questions.
        Saved chats may contain answers from earlier bank selections.
      </div>

      {status && !status.configured && (
        <div className="ai-setup-banner">
          <div>
            <strong>Wally can still answer factual questions locally</strong>
            <span>Connect Groq for advice, planning and deeper explanations. Local questions use 0 AI tokens.</span>
          </div>
          <code>GROQ_API_KEY=your_key_here</code>
        </div>
      )}

      <div className={`wally-workspace ${historyCollapsed ? 'history-collapsed' : ''}`}>
        {historyOpen && <button className="wally-history-backdrop" aria-label="Close chat history" onClick={() => setHistoryOpen(false)} />}
        <aside className={`wally-history ${historyOpen ? 'open' : ''}`}>
          <div className="wally-history-head">
            <div>
              <span>Saved chats</span>
              <small>Conversations save automatically</small>
            </div>
            <div className="wally-history-head-actions">
              <button
                className="wally-history-collapse"
                type="button"
                onClick={collapseHistory}
                aria-label="Collapse saved chats"
                title="Collapse saved chats"
              >
                <PanelLeftClose size={18} />
              </button>
              <button className="wally-history-close" type="button" onClick={() => setHistoryOpen(false)} aria-label="Close history"><X size={18} /></button>
            </div>
          </div>
          <button type="button" className="wally-new-chat" onClick={startNewChat}>
            <Plus size={17} /> New chat
          </button>
          <div className="wally-chat-list">
            {chats.length === 0 ? (
              <div className="wally-chat-empty">
                <MessageCircle size={19} />
                <span>Your saved chats will appear here.</span>
              </div>
            ) : chats.map((chat) => (
              <div key={chat.id} className={`wally-chat-row ${chat.id === activeChatId ? 'active' : ''}`}>
                <button type="button" className="wally-chat-open" onClick={() => openChat(chat)}>
                  <span className="wally-chat-row-copy">
                    <strong>{chat.title}</strong>
                    {chat.summary && <span className="wally-chat-summary">{chat.summary}</span>}
                    <small>{formatChatDate(chat.updatedAt)} · {Math.ceil((chat.messageCount || 0) / 2)} {Math.ceil((chat.messageCount || 0) / 2) === 1 ? 'reply' : 'replies'}</small>
                  </span>
                </button>
                <button
                  type="button"
                  className="wally-chat-delete"
                  title="Delete chat"
                  aria-label={`Delete ${chat.title}`}
                  onClick={(event) => deleteChat(event, chat)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </aside>

        <section className="wally-chat-main">
          <div className="wally-chat-toolbar">
            <button
              type="button"
              className={`wally-history-trigger ${historyCollapsed ? 'desktop-visible' : ''}`}
              onClick={expandHistory}
            >
              {historyCollapsed ? <PanelLeftOpen size={17} /> : <History size={17} />} Chats
            </button>
            <div className="wally-active-chat">
              <strong>{activeChatTitle}</strong>
              <span><Check size={13} /> {activeChatId ? 'Saved' : 'Saves after Wally replies'}</span>
            </div>
            <button type="button" className="wally-sound-toggle" onClick={toggleSounds} title={soundOn ? 'Mute interface sounds' : 'Turn interface sounds on'}>
              {soundOn ? <Volume2 size={18} /> : <VolumeX size={18} />}
            </button>
          </div>

          <div className="wally-chat-scroll">
            {loadingChat ? (
              <div className="wally-chat-loading"><div className="ai-thinking-dots"><i /><i /><i /></div><span>Opening saved chat…</span></div>
            ) : messages.length === 0 ? (
              <section className="ai-welcome-card">
                <div className="ai-welcome-copy">
                  <span className="ai-kicker">Meet Wally</span>
                  <h2>What would you like to figure out?</h2>
                  <p>
                    Ask Wally about something you want to buy, a savings goal, a spending change, or how to make your budget work better.
                    Your full bank statement is never sent to the AI.
                  </p>
                </div>
                <div className="ai-starter-grid">
                  {STARTER_QUESTIONS.map((item) => (
                    <button key={item} type="button" onClick={() => submit(item)} disabled={!status?.configured || sending} title={!status?.configured ? 'Connect Groq for planning questions' : undefined}>
                      {item}
                    </button>
                  ))}
                </div>
              </section>
            ) : (
              <section className="ai-conversation" aria-live="polite">
                {messages.map((message, index) => (
                  <article key={message.id || `${message.role}-${index}`} className={`ai-message ${message.role}`}>
                    <div className="ai-message-label">{message.role === 'user' ? 'You' : 'Wally'}</div>
                    {message.role === 'user' ? (
                      <div className="ai-message-body">{message.content}</div>
                    ) : (
                      <>
                        <StructuredAnswer answer={message.answer} onUseFollowUp={useFollowUp} />
                        <PrivacyDetails dataUsed={message.dataUsed} />
                      </>
                    )}
                  </article>
                ))}
                {sending && (
                  <article className="ai-message assistant ai-thinking">
                    <div className="ai-message-label">Wally</div>
                    <div className="ai-thinking-dots"><i /><i /><i /></div>
                  </article>
                )}
                <div ref={bottomRef} />
              </section>
            )}
          </div>

          {error && <div className="ai-error">{error}</div>}

          <div className="ai-composer-shell">
            <div className="ai-composer">
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask Wally something about your money…"
                rows={2}
                disabled={sending}
              />
              <button
                type="button"
                className="ai-send-button"
                onClick={() => submit()}
                disabled={sending || !question.trim()}
                aria-label="Send question"
              >
                <ArrowUp size={19} />
              </button>
            </div>
            <p>Planning support based on your imported data. Check important financial decisions before acting.</p>
          </div>
        </section>
      </div>
    </div>
  );
}
