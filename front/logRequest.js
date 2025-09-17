// logRequest.js
const { MongoClient } = require('mongodb');
require('dotenv').config();

const uri = process.env.MONGODB_URI;
const client = new MongoClient(uri);

async function logRequest(ip, path, data) {
    try {
        await client.connect();
        const db = client.db('pcss');
        const logs = db.collection('logs');

        const dateKey = new Date().toISOString().split('T')[0]; // YYYY-MM-DD

        const logEntry = {
            time: new Date().toISOString(),
            ip,
            path,
            data,  // globalInputData 저장
        };

        await logs.updateOne(
            { date: dateKey },
            { $push: { logs: logEntry } },
            { upsert: true }
        );
    } catch (err) {
        console.error('MongoDB logging error:', err.message);
    }
}

module.exports = logRequest;
