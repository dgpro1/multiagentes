-- Tenant baseline: the data plane as of central migration 0071_client_data_stores,
-- created in a client's own database (schema hunterai, through search_path).
-- Frozen on purpose: never edit. Changes to data-plane models need a new
-- tNNNN_*.sql file (see app/services/tenant_schema.py and tests/test_data_plane.py).

CREATE TABLE client_resources (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT DEFAULT '' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	position INTEGER DEFAULT '0' NOT NULL, 
	storage_key VARCHAR(300), 
	media_kind VARCHAR(10), 
	mime VARCHAR(120), 
	filename VARCHAR(255), 
	size_bytes INTEGER DEFAULT '0' NOT NULL, 
	url VARCHAR(2048), 
	message_template TEXT DEFAULT '' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_client_resources_client_name UNIQUE (client_id, name)
);

CREATE INDEX ix_client_resources_agency_id ON client_resources (agency_id);

CREATE INDEX ix_client_resources_client_id ON client_resources (client_id);

CREATE TABLE contact_tags (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	color VARCHAR(20) DEFAULT '#6b7280' NOT NULL, 
	route_team_id UUID, 
	route_assignee_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_contact_tags_client_id ON contact_tags (client_id);

CREATE INDEX ix_contact_tags_route_assignee_id ON contact_tags (route_assignee_id);

CREATE INDEX ix_contact_tags_route_team_id ON contact_tags (route_team_id);

CREATE UNIQUE INDEX uq_contact_tags_client_name ON contact_tags (client_id, lower(name));

CREATE TABLE contacts (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	name VARCHAR(180) NOT NULL, 
	whatsapp_contact_name VARCHAR(180), 
	whatsapp_contact_updated_at TIMESTAMP WITH TIME ZONE, 
	phone VARCHAR(40), 
	email VARCHAR(255), 
	company VARCHAR(160), 
	notes TEXT NOT NULL, 
	blocked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_contacts_client_id ON contacts (client_id);

CREATE UNIQUE INDEX uq_contacts_client_phone ON contacts (client_id, phone) WHERE phone IS NOT NULL;

CREATE TABLE lead_fields (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	key VARCHAR(60) NOT NULL, 
	label VARCHAR(80) NOT NULL, 
	type VARCHAR(12) NOT NULL, 
	options JSON DEFAULT '[]' NOT NULL, 
	position INTEGER DEFAULT '0' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_lead_fields_client_key UNIQUE (client_id, key)
);

CREATE INDEX ix_lead_fields_agency_id ON lead_fields (agency_id);

CREATE INDEX ix_lead_fields_client_id ON lead_fields (client_id);

CREATE TABLE pipeline_stages (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	color VARCHAR(16) DEFAULT '#2f6df0' NOT NULL, 
	position INTEGER DEFAULT '0' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_pipeline_stages_client_name UNIQUE (client_id, name)
);

CREATE INDEX ix_pipeline_stages_client_id ON pipeline_stages (client_id);

CREATE TABLE professionals (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	role VARCHAR(120) DEFAULT '' NOT NULL, 
	color VARCHAR(7) DEFAULT '#2f6df0' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	slot_minutes INTEGER DEFAULT '30' NOT NULL, 
	weekly_hours JSON DEFAULT '{}' NOT NULL, 
	assignment_notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_professionals_agency_id ON professionals (agency_id);

CREATE INDEX ix_professionals_client_id ON professionals (client_id);

CREATE TABLE services (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	name VARCHAR(180) NOT NULL, 
	description TEXT DEFAULT '' NOT NULL, 
	price FLOAT DEFAULT '0' NOT NULL, 
	currency VARCHAR(3) DEFAULT 'USD' NOT NULL, 
	duration_minutes INTEGER DEFAULT '30' NOT NULL, 
	modality VARCHAR(32) DEFAULT 'presencial' NOT NULL, 
	requires_deposit BOOLEAN DEFAULT 'false' NOT NULL, 
	deposit_amount FLOAT, 
	requirements TEXT DEFAULT '' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	position INTEGER DEFAULT '0' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_services_agency_id ON services (agency_id);

CREATE INDEX ix_services_client_id ON services (client_id);

CREATE TABLE contact_identities (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	contact_id UUID NOT NULL, 
	provider VARCHAR(30) NOT NULL, 
	external_account_id VARCHAR(128) DEFAULT '' NOT NULL, 
	external_user_id VARCHAR(255) NOT NULL, 
	username VARCHAR(180), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE, 
	CONSTRAINT uq_contact_identity UNIQUE (client_id, provider, external_account_id, external_user_id)
);

CREATE INDEX ix_contact_identities_client_id ON contact_identities (client_id);

CREATE INDEX ix_contact_identities_contact_id ON contact_identities (contact_id);

CREATE TABLE contact_tag_links (
	contact_id UUID NOT NULL, 
	tag_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (contact_id, tag_id), 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE, 
	FOREIGN KEY(tag_id) REFERENCES contact_tags (id) ON DELETE CASCADE
);

CREATE INDEX ix_contact_tag_links_tag_id ON contact_tag_links (tag_id);

CREATE TABLE conversations (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	number INTEGER NOT NULL, 
	agent_id UUID NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	mode VARCHAR(30) NOT NULL, 
	channel VARCHAR(40) NOT NULL, 
	social_channel_id UUID, 
	social_last_inbound_at TIMESTAMP WITH TIME ZONE, 
	social_reply_due_at TIMESTAMP WITH TIME ZONE, 
	social_reply_claimed_until TIMESTAMP WITH TIME ZONE, 
	social_thread_owned BOOLEAN DEFAULT 'true' NOT NULL, 
	social_pending_escalation JSON, 
	whatsapp_channel_id UUID, 
	whatsapp_cloud_channel_id UUID, 
	widget_channel_id UUID, 
	external_chat_id VARCHAR(255), 
	provider_conversation_id VARCHAR(255), 
	contact_name VARCHAR(180), 
	contact_id UUID, 
	operator_read_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) DEFAULT 'open' NOT NULL, 
	status_changed_at TIMESTAMP WITH TIME ZONE, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	first_reply_at TIMESTAMP WITH TIME ZONE, 
	phone_pause_until TIMESTAMP WITH TIME ZONE, 
	phone_resume_claimed_until TIMESTAMP WITH TIME ZONE, 
	taken_over_at TIMESTAMP WITH TIME ZONE, 
	assignee_id UUID, 
	assigned_at TIMESTAMP WITH TIME ZONE, 
	team_id UUID, 
	pipeline_stage_id UUID, 
	deal_value NUMERIC(12, 2), 
	responsible_id UUID, 
	custom_values JSON DEFAULT '{}' NOT NULL, 
	primary_conversation_id UUID, 
	waiting_since TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
	FOREIGN KEY(pipeline_stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL, 
	FOREIGN KEY(primary_conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	CONSTRAINT uq_conversations_client_number UNIQUE (client_id, number)
);

CREATE INDEX ix_conversations_agency_id ON conversations (agency_id);

CREATE INDEX ix_conversations_agent_id ON conversations (agent_id);

CREATE INDEX ix_conversations_archived_at ON conversations (archived_at);

CREATE INDEX ix_conversations_assignee_id ON conversations (assignee_id);

CREATE INDEX ix_conversations_client_id ON conversations (client_id);

CREATE INDEX ix_conversations_contact_id ON conversations (contact_id);

CREATE INDEX ix_conversations_phone_pause_until ON conversations (phone_pause_until);

CREATE INDEX ix_conversations_pipeline_stage_id ON conversations (pipeline_stage_id);

CREATE INDEX ix_conversations_primary_conversation_id ON conversations (primary_conversation_id);

CREATE INDEX ix_conversations_provider_conversation_id ON conversations (provider_conversation_id);

CREATE INDEX ix_conversations_responsible_id ON conversations (responsible_id);

CREATE INDEX ix_conversations_social_channel_id ON conversations (social_channel_id);

CREATE INDEX ix_conversations_social_chat ON conversations (social_channel_id, external_chat_id);

CREATE INDEX ix_conversations_social_reply_due_at ON conversations (social_reply_due_at);

CREATE INDEX ix_conversations_team_id ON conversations (team_id);

CREATE INDEX ix_conversations_whatsapp_channel_id ON conversations (whatsapp_channel_id);

CREATE INDEX ix_conversations_whatsapp_chat ON conversations (whatsapp_channel_id, external_chat_id);

CREATE INDEX ix_conversations_whatsapp_cloud_channel_id ON conversations (whatsapp_cloud_channel_id);

CREATE INDEX ix_conversations_whatsapp_cloud_chat ON conversations (whatsapp_cloud_channel_id, external_chat_id);

CREATE INDEX ix_conversations_widget_channel_id ON conversations (widget_channel_id);

CREATE TABLE professional_services (
	professional_id UUID NOT NULL, 
	service_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (professional_id, service_id), 
	FOREIGN KEY(professional_id) REFERENCES professionals (id) ON DELETE CASCADE, 
	FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE CASCADE
);

CREATE INDEX ix_professional_services_service_id ON professional_services (service_id);

CREATE TABLE appointments (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	conversation_id UUID, 
	contact_id UUID, 
	professional_id UUID, 
	service_id UUID, 
	title VARCHAR(160) NOT NULL, 
	start_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	end_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	status VARCHAR(32) DEFAULT 'confirmed' NOT NULL, 
	notes TEXT, 
	created_by_role VARCHAR(32) DEFAULT 'operator' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE SET NULL, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
	FOREIGN KEY(professional_id) REFERENCES professionals (id) ON DELETE SET NULL, 
	FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE SET NULL
);

CREATE INDEX ix_appointments_agency_id ON appointments (agency_id);

CREATE INDEX ix_appointments_client_id ON appointments (client_id);

CREATE INDEX ix_appointments_contact_id ON appointments (contact_id);

CREATE INDEX ix_appointments_conversation_id ON appointments (conversation_id);

CREATE INDEX ix_appointments_end_time ON appointments (end_time);

CREATE INDEX ix_appointments_professional_id ON appointments (professional_id);

CREATE INDEX ix_appointments_service_id ON appointments (service_id);

CREATE INDEX ix_appointments_start_time ON appointments (start_time);

CREATE INDEX ix_appointments_status ON appointments (status);

CREATE TABLE messages (
	id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	role VARCHAR(30) NOT NULL, 
	kind VARCHAR(20) DEFAULT 'message' NOT NULL, 
	is_historical BOOLEAN DEFAULT 'false' NOT NULL, 
	activity JSON, 
	content TEXT NOT NULL, 
	llm_content TEXT, 
	sources JSON NOT NULL, 
	tool_calls JSON, 
	sender_type VARCHAR(30) NOT NULL, 
	sender_name VARCHAR(180), 
	portal_user_id UUID, 
	external_message_id VARCHAR(1024), 
	delivery_status VARCHAR(20), 
	delivery_error TEXT, 
	reaction VARCHAR(16), 
	incoming_reaction VARCHAR(16), 
	quoted_message_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(quoted_message_id) REFERENCES messages (id) ON DELETE SET NULL, 
	CONSTRAINT uq_messages_conversation_external UNIQUE (conversation_id, external_message_id)
);

CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE INDEX ix_messages_external_message_id ON messages (external_message_id);

CREATE TABLE scheduled_messages (
	id UUID NOT NULL, 
	agency_id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	via_conversation_id UUID, 
	portal_user_id UUID, 
	sender_type VARCHAR(20) DEFAULT 'human' NOT NULL, 
	sender_name VARCHAR(180), 
	content TEXT NOT NULL, 
	scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(20) DEFAULT 'pending' NOT NULL, 
	failure_reason TEXT, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
	FOREIGN KEY(via_conversation_id) REFERENCES conversations (id) ON DELETE SET NULL
);

CREATE INDEX ix_scheduled_messages_agency_id ON scheduled_messages (agency_id);

CREATE INDEX ix_scheduled_messages_client_id ON scheduled_messages (client_id);

CREATE INDEX ix_scheduled_messages_conversation_id ON scheduled_messages (conversation_id);

CREATE INDEX ix_scheduled_messages_portal_user_id ON scheduled_messages (portal_user_id);

CREATE INDEX ix_scheduled_messages_scheduled_for ON scheduled_messages (scheduled_for);

CREATE INDEX ix_scheduled_messages_status ON scheduled_messages (status);

CREATE INDEX ix_scheduled_messages_via_conversation_id ON scheduled_messages (via_conversation_id);

CREATE TABLE message_attachments (
	id UUID NOT NULL, 
	message_id UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	mime VARCHAR(100) NOT NULL, 
	filename VARCHAR(255), 
	size_bytes INTEGER NOT NULL, 
	data BYTEA, 
	storage_key VARCHAR(300), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_attachments_message_id ON message_attachments (message_id);
