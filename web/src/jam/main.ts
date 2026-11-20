// Jam guest page (served by the server at /jam/<code>): no account, no player — see api/openapi.yaml "jam".
import { mount } from 'svelte';
import '../app.css';
import JamGuest from './JamGuest.svelte';

export default mount(JamGuest, { target: document.getElementById('app')! });
